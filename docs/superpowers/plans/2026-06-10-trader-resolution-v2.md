# Trader Resolution v2 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementare la risoluzione trader dinamica per topic multi-trader, basata su alias per-topic, pattern extractor hardcoded, e reply/link chain con lettura di `resolved_trader_id`.

**Architecture:** Un singolo `TraderResolver` sostituisce `EffectiveTraderResolver` + `RuntimeV2TraderResolver`. L'ordine di priorità è: config statico → alias nel testo → pattern extractor → reply/link chain → review. Gli alias sono dichiarati per-topic in `channels.yaml`; la reply chain legge `resolved_trader_id ?? source_trader_id` via `RawMessageRepository`.

**Tech Stack:** Python 3.12+, Pydantic v2, SQLite, PyYAML, pytest

---

## File Map

| File | Azione | Responsabilità |
|---|---|---|
| `src/runtime_v2/trader_resolution/channel_config_resolver.py` | Modifica | Aggiunge `aliases` e `resolution_max_depth` a `ChannelEntry` |
| `src/runtime_v2/persistence/raw_messages.py` | Modifica | Aggiunge `ChainNode` + `get_chain_node()` |
| `src/runtime_v2/trader_resolution/models.py` | Modifica | Aggiunge `"link"` e `"link_multi"` a `ResolutionMethod` |
| `src/telegram/pattern_extractors.py` | Crea | Pattern rules hardcoded per topic speciali |
| `src/telegram/trader_resolver.py` | Crea | `TraderResolver` — risoluzione completa a cascata |
| `src/telegram/listener.py` | Modifica | `_process_item()` chiama `TraderResolver`, scrive `resolved_trader_id` |
| `config/channels.yaml` | Modifica | Aggiunge blocco `resolution:` per topic multi-trader |
| `tests/runtime_v2/test_channel_config_resolver.py` | Modifica | Aggiunge test per aliases/max_depth |
| `tests/runtime_v2/test_raw_message_repository.py` | Modifica | Aggiunge test per `get_chain_node` |
| `tests/telegram/test_trader_resolver.py` | Crea | Test completi per `TraderResolver` |
| `src/telegram/effective_trader.py` | Depreca | Sostituito da `TraderResolver` |
| `src/runtime_v2/trader_resolution/resolver.py` | Depreca | Sostituito da `TraderResolver` |
| `config/telegram_source_map.json` | Rimuovi | Non più usato |

---

## Task 1: `ChannelConfigResolver` — alias e max_depth per-topic

**Files:**
- Modify: `src/runtime_v2/trader_resolution/channel_config_resolver.py`
- Modify: `tests/runtime_v2/test_channel_config_resolver.py`

- [ ] **Step 1: Scrivi i test per aliases e max_depth**

Aggiungi in fondo a `tests/runtime_v2/test_channel_config_resolver.py`:

```python
_MULTI_TRADER_YAML = """
channels:
  - chat_id: -1009999999999
    topic_id: 9
    label: "MultiTopic"
    active: true
    trader_id: null
    parser_profile: null
    resolution:
      max_depth: 3
      aliases:
        "trader#a": trader_a
        "trader#b": trader_b
    blacklist: []
  - chat_id: -1009999999999
    topic_id: 10
    label: "SingleTopic"
    active: true
    trader_id: trader_c
    blacklist: []
"""


@pytest.fixture
def multi_config_file(tmp_path):
    p = tmp_path / "channels.yaml"
    p.write_text(_MULTI_TRADER_YAML)
    return str(p)


@pytest.fixture
def multi_resolver(multi_config_file):
    return ChannelConfigResolver(multi_config_file)


def test_multi_trader_topic_has_null_trader_id(multi_resolver):
    entry = multi_resolver.lookup("-1009999999999", topic_id=9)
    assert entry is not None
    assert entry.trader_id is None


def test_multi_trader_topic_aliases_loaded(multi_resolver):
    entry = multi_resolver.lookup("-1009999999999", topic_id=9)
    assert entry is not None
    assert entry.aliases == {"trader#a": "trader_a", "trader#b": "trader_b"}


def test_multi_trader_topic_max_depth(multi_resolver):
    entry = multi_resolver.lookup("-1009999999999", topic_id=9)
    assert entry is not None
    assert entry.resolution_max_depth == 3


def test_single_trader_topic_empty_aliases(multi_resolver):
    entry = multi_resolver.lookup("-1009999999999", topic_id=10)
    assert entry is not None
    assert entry.aliases == {}
    assert entry.resolution_max_depth == 5  # default


def test_existing_entries_unaffected_by_aliases_field(resolver):
    # Verifica che gli entry senza resolution abbiano aliases vuote e max_depth=5
    entry = resolver.lookup("-1001111111111", topic_id=3)
    assert entry is not None
    assert entry.aliases == {}
    assert entry.resolution_max_depth == 5
```

- [ ] **Step 2: Esegui i test per verificare che falliscano**

```
pytest tests/runtime_v2/test_channel_config_resolver.py -k "aliases or max_depth or multi_trader or empty_aliases" -v
```
Expected: FAIL con `AttributeError: aliases`

- [ ] **Step 3: Aggiorna `ChannelEntry` e `reload()` in `channel_config_resolver.py`**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

from src.core.trader_tags import normalize_trader_tag


@dataclass(slots=True, frozen=True)
class ChannelEntry:
    chat_id: str
    topic_id: int | None
    label: str | None
    active: bool
    trader_id: str | None
    parser_profile: str  # defaults to trader_id when not overridden in yaml
    blacklist: list[str]
    aliases: dict[str, str]          # normalized tag → trader_id; empty for single-trader
    resolution_max_depth: int        # default 5; used only when trader_id is None


class ChannelConfigResolver:
    """Loads channels.yaml and provides O(1) lookup by (source_chat_id, topic_id).

    Call reload() to refresh after a file change.
    """

    def __init__(self, config_path: str) -> None:
        self._config_path = config_path
        self._index: dict[tuple[str, int | None], ChannelEntry] = {}
        self._global_blacklist: list[str] = []
        self.reload()

    def reload(self) -> None:
        with open(self._config_path, encoding="utf-8") as f:
            data: dict[str, Any] = yaml.safe_load(f)
        index: dict[tuple[str, int | None], ChannelEntry] = {}
        for raw in data.get("channels", []):
            chat_id = str(raw["chat_id"])
            topic_id: int | None = raw.get("topic_id")
            trader_id: str | None = raw.get("trader_id")
            parser_profile: str = raw.get("parser_profile") or trader_id or ""
            resolution = raw.get("resolution") or {}
            aliases_raw: dict[str, str] = resolution.get("aliases") or {}
            aliases: dict[str, str] = {}
            for alias_key, alias_trader in aliases_raw.items():
                normalized = normalize_trader_tag(str(alias_key))
                if normalized:
                    aliases[normalized] = str(alias_trader)
            max_depth = int(resolution.get("max_depth", 5))
            entry = ChannelEntry(
                chat_id=chat_id,
                topic_id=topic_id,
                label=raw.get("label"),
                active=bool(raw.get("active", False)),
                trader_id=trader_id,
                parser_profile=parser_profile,
                blacklist=list(raw.get("blacklist", [])),
                aliases=aliases,
                resolution_max_depth=max_depth,
            )
            index[(chat_id, topic_id)] = entry
        self._index = index
        self._global_blacklist = list(data.get("blacklist_global", []))

    def lookup(self, source_chat_id: str, topic_id: int | None) -> ChannelEntry | None:
        entry = self._index.get((source_chat_id, topic_id))
        if entry is not None:
            return entry
        if topic_id is not None:
            return self._index.get((source_chat_id, None))
        return None

    def is_globally_blacklisted(self, text: str) -> bool:
        return any(phrase in text for phrase in self._global_blacklist)
```

- [ ] **Step 4: Esegui tutti i test del resolver**

```
pytest tests/runtime_v2/test_channel_config_resolver.py -v
```
Expected: tutti PASS

- [ ] **Step 5: Commit**

```
git add src/runtime_v2/trader_resolution/channel_config_resolver.py tests/runtime_v2/test_channel_config_resolver.py
git commit -m "feat: add aliases and resolution_max_depth to ChannelEntry"
```

---

## Task 2: `RawMessageRepository` — `get_chain_node`

**Files:**
- Modify: `src/runtime_v2/persistence/raw_messages.py`
- Modify: `tests/runtime_v2/test_raw_message_repository.py`

- [ ] **Step 1: Scrivi il test**

Aggiungi in fondo a `tests/runtime_v2/test_raw_message_repository.py`:

```python
from src.runtime_v2.persistence.raw_messages import ChainNode


def test_get_chain_node_returns_none_for_unknown(repo):
    result = repo.get_chain_node("-100123", 9999)
    assert result is None


def test_get_chain_node_returns_node(repo):
    item = _make_item(chat_id="-100123", msg_id=100)
    env = repo.save_raw(item)
    # Scrivi resolved_trader_id manualmente
    conn = __import__("sqlite3").connect(repo._db_path)
    conn.execute(
        "UPDATE raw_messages SET resolved_trader_id=? WHERE raw_message_id=?",
        ("trader_a", env.raw_message_id),
    )
    conn.commit()
    conn.close()
    node = repo.get_chain_node("-100123", 100)
    assert node is not None
    assert node.resolved_trader_id == "trader_a"
    assert node.source_trader_id is None
    assert node.reply_to_message_id is None


def test_get_chain_node_source_trader_id(repo):
    conn = __import__("sqlite3").connect(repo._db_path)
    conn.execute(
        "INSERT INTO raw_messages (source_chat_id, telegram_message_id, source_trader_id, "
        "raw_text, reply_to_message_id, message_ts, acquired_at, acquisition_status) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("-100123", 200, "trader_b", "some text", 150,
         "2026-01-01T00:00:00", "2026-01-01T00:00:00", "ACQUIRED"),
    )
    conn.commit()
    conn.close()
    node = repo.get_chain_node("-100123", 200)
    assert node is not None
    assert node.source_trader_id == "trader_b"
    assert node.raw_text == "some text"
    assert node.reply_to_message_id == 150
```

- [ ] **Step 2: Esegui il test per verificare che fallisca**

```
pytest tests/runtime_v2/test_raw_message_repository.py -k "chain_node" -v
```
Expected: FAIL con `ImportError: cannot import name 'ChainNode'`

- [ ] **Step 3: Aggiungi `ChainNode` e `get_chain_node` a `raw_messages.py`**

Aggiungi dopo gli import esistenti in `src/runtime_v2/persistence/raw_messages.py`:

```python
from dataclasses import dataclass


@dataclass(slots=True)
class ChainNode:
    """Minimal view of a raw message for reply-chain walking."""
    source_trader_id: str | None
    resolved_trader_id: str | None
    raw_text: str | None
    reply_to_message_id: int | None
```

Aggiungi il metodo alla classe `RawMessageRepository`:

```python
def get_chain_node(self, source_chat_id: str, telegram_message_id: int) -> ChainNode | None:
    """Read the minimal fields needed for reply-chain resolution."""
    conn = sqlite3.connect(self._db_path)
    row = conn.execute(
        "SELECT source_trader_id, resolved_trader_id, raw_text, reply_to_message_id "
        "FROM raw_messages "
        "WHERE source_chat_id = ? AND telegram_message_id = ? "
        "LIMIT 1",
        (source_chat_id, telegram_message_id),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return ChainNode(
        source_trader_id=row[0],
        resolved_trader_id=row[1],
        raw_text=row[2],
        reply_to_message_id=int(row[3]) if row[3] is not None else None,
    )
```

- [ ] **Step 4: Esegui i test**

```
pytest tests/runtime_v2/test_raw_message_repository.py -v
```
Expected: tutti PASS

- [ ] **Step 5: Commit**

```
git add src/runtime_v2/persistence/raw_messages.py tests/runtime_v2/test_raw_message_repository.py
git commit -m "feat: add ChainNode and get_chain_node to RawMessageRepository"
```

---

## Task 3: Aggiorna `ResolutionMethod` in `models.py`

**Files:**
- Modify: `src/runtime_v2/trader_resolution/models.py`

- [ ] **Step 1: Aggiungi `"link"` e `"link_multi"` a `ResolutionMethod`**

```python
ResolutionMethod = Literal[
    "content_alias",
    "content_alias_ambiguous",
    "reply_chain",
    "reply_chain_alias",
    "source_chat_id",
    "source_chat_username",
    "source_chat_title",
    "source_topic_config",
    "assume_trader",
    "link",
    "link_multi",
    "unresolved",
]
```

- [ ] **Step 2: Verifica che i test esistenti passino ancora**

```
pytest tests/runtime_v2/test_trader_resolution_models.py -v
```
Expected: PASS

- [ ] **Step 3: Commit**

```
git add src/runtime_v2/trader_resolution/models.py
git commit -m "feat: add link and link_multi to ResolutionMethod"
```

---

## Task 4: `pattern_extractors.py`

**Files:**
- Create: `src/telegram/pattern_extractors.py`
- Create: `tests/telegram/__init__.py`
- Create: `tests/telegram/test_pattern_extractors.py`

- [ ] **Step 1: Crea `tests/telegram/__init__.py`**

File vuoto:
```python
```

- [ ] **Step 2: Scrivi i test**

`tests/telegram/test_pattern_extractors.py`:

```python
from __future__ import annotations
import pytest
from src.telegram.pattern_extractors import extract_trader_by_pattern

_INTRADAY_MSG = (
    "Стратегия «RSI(2) Коннора» открыла ЛОНГ по XLM · интрадей (1H)\n"
    "Вход 0.18581, стоп 0.18054, цель 0.19772"
)
_SWING_MSG = (
    "Стратегия «RSI(2) Коннора» открыла ЛОНГ по TON · свинг (4H)\n"
    "Вход 1.66, стоп 1.60, цель 1.81"
)
_UNRELATED_MSG = "BUY BTC at 45000 sl 44000 tp 47000"


def test_rsi_intraday_recognized(rsi_topic_id):
    assert extract_trader_by_pattern(rsi_topic_id, _INTRADAY_MSG) == "trader_rsi_intraday"


def test_rsi_swing_recognized(rsi_topic_id):
    assert extract_trader_by_pattern(rsi_topic_id, _SWING_MSG) == "trader_rsi_swing"


def test_unrelated_message_returns_none(rsi_topic_id):
    assert extract_trader_by_pattern(rsi_topic_id, _UNRELATED_MSG) is None


def test_unknown_topic_returns_none():
    assert extract_trader_by_pattern(9999, _INTRADAY_MSG) is None


def test_empty_text_returns_none(rsi_topic_id):
    assert extract_trader_by_pattern(rsi_topic_id, "") is None


@pytest.fixture
def rsi_topic_id():
    # Usa il topic_id reale configurato in pattern_extractors.py
    from src.telegram.pattern_extractors import RSI_TOPIC_ID
    return RSI_TOPIC_ID
```

- [ ] **Step 3: Esegui i test per verificare che falliscano**

```
pytest tests/telegram/test_pattern_extractors.py -v
```
Expected: FAIL con `ModuleNotFoundError`

- [ ] **Step 4: Crea `src/telegram/pattern_extractors.py`**

```python
"""Hardcoded pattern-based trader extraction for special topics.

Used as fallback when alias lookup in channels.yaml finds no match.
Add new cases here only when a topic uses derived trader identity
(e.g. strategy + timeframe) rather than explicit trader tags.
"""

from __future__ import annotations

# Topic ID del canale RSI multi-strategia.
# Aggiornare se il topic cambia.
RSI_TOPIC_ID = 9


def extract_trader_by_pattern(topic_id: int, text: str) -> str | None:
    """Return trader_id derived from message content patterns, or None if no match."""
    if not text:
        return None
    if topic_id == RSI_TOPIC_ID:
        if "«RSI(2) Коннора»" in text and "интрадей" in text:
            return "trader_rsi_intraday"
        if "«RSI(2) Коннора»" in text and "свинг" in text:
            return "trader_rsi_swing"
    return None
```

- [ ] **Step 5: Esegui i test**

```
pytest tests/telegram/test_pattern_extractors.py -v
```
Expected: tutti PASS

- [ ] **Step 6: Commit**

```
git add src/telegram/pattern_extractors.py tests/telegram/__init__.py tests/telegram/test_pattern_extractors.py
git commit -m "feat: add pattern_extractors for hardcoded topic-based trader identification"
```

---

## Task 5: `TraderResolver` — implementazione completa

**Files:**
- Create: `src/telegram/trader_resolver.py`
- Create: `tests/telegram/test_trader_resolver.py`

- [ ] **Step 1: Scrivi i test**

`tests/telegram/test_trader_resolver.py`:

```python
from __future__ import annotations
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from src.telegram.trader_resolver import TraderResolver
from src.runtime_v2.trader_resolution.channel_config_resolver import ChannelEntry
from src.runtime_v2.intake.models import RawMessageEnvelope
from src.runtime_v2.persistence.raw_messages import ChainNode

_TS = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


def _envelope(
    chat_id: str = "-100123",
    topic_id: int | None = 9,
    text: str | None = "buy btc",
    reply_id: int | None = None,
    raw_msg_id: int = 1,
) -> RawMessageEnvelope:
    return RawMessageEnvelope(
        raw_message_id=raw_msg_id,
        source_chat_id=chat_id,
        source_chat_title="Test",
        source_type="channel",
        source_topic_id=topic_id,
        telegram_message_id=500,
        reply_to_message_id=reply_id,
        raw_text=text,
        message_ts=_TS,
        acquired_at=_TS,
        acquisition_mode="live",
        acquisition_status="ACQUIRED",
        processing_status="pending",
        source_trader_id=None,
        resolved_trader_id=None,
        resolution_method=None,
        resolution_detail=None,
        has_media=False,
        media_kind=None,
        media_mime_type=None,
        media_filename=None,
    )


def _entry(trader_id: str | None, topic_id: int | None = 9, aliases: dict | None = None, max_depth: int = 5) -> ChannelEntry:
    return ChannelEntry(
        chat_id="-100123",
        topic_id=topic_id,
        label="Test",
        active=True,
        trader_id=trader_id,
        parser_profile=trader_id or "",
        blacklist=[],
        aliases=aliases or {},
        resolution_max_depth=max_depth,
    )


@pytest.fixture
def channel_config():
    return MagicMock()


@pytest.fixture
def raw_repo():
    return MagicMock()


@pytest.fixture
def resolver(channel_config, raw_repo):
    return TraderResolver(channel_config=channel_config, raw_repo=raw_repo)


# --- Step 1: config statico ---

def test_config_single_trader_chat(resolver, channel_config):
    channel_config.lookup.return_value = _entry("trader_a", topic_id=None)
    ctx = resolver.resolve(_envelope(topic_id=None))
    assert ctx.trader_id == "trader_a"
    assert ctx.method == "source_chat_id"
    assert not ctx.is_ambiguous


def test_config_single_trader_topic(resolver, channel_config):
    channel_config.lookup.return_value = _entry("trader_a", topic_id=9)
    ctx = resolver.resolve(_envelope(topic_id=9))
    assert ctx.trader_id == "trader_a"
    assert ctx.method == "source_topic_config"


# --- Step 2: alias nel testo ---

def test_alias_in_text_resolved(resolver, channel_config):
    channel_config.lookup.return_value = _entry(None, aliases={"trader#a": "trader_a"})
    ctx = resolver.resolve(_envelope(text="Trader #A signal buy btc"))
    assert ctx.trader_id == "trader_a"
    assert ctx.method == "content_alias"


def test_alias_ambiguous_two_tags(resolver, channel_config):
    channel_config.lookup.return_value = _entry(
        None, aliases={"trader#a": "trader_a", "trader#b": "trader_b"}
    )
    ctx = resolver.resolve(_envelope(text="[trader#A] e [trader#B]"))
    assert ctx.trader_id is None
    assert ctx.is_ambiguous is True
    assert ctx.method == "content_alias_ambiguous"


def test_alias_same_trader_twice_not_ambiguous(resolver, channel_config):
    channel_config.lookup.return_value = _entry(None, aliases={"trader#a": "trader_a"})
    ctx = resolver.resolve(_envelope(text="[trader#A] buy btc trader #A confirmed"))
    assert ctx.trader_id == "trader_a"
    assert not ctx.is_ambiguous


# --- Step 3: reply chain ---

def test_reply_chain_resolved_trader_id(resolver, channel_config, raw_repo):
    channel_config.lookup.return_value = _entry(None, aliases={})
    raw_repo.get_chain_node.return_value = ChainNode(
        source_trader_id=None,
        resolved_trader_id="trader_b",
        raw_text="old signal",
        reply_to_message_id=None,
    )
    ctx = resolver.resolve(_envelope(text="sl moved", reply_id=42))
    assert ctx.trader_id == "trader_b"
    assert ctx.method == "reply_chain"
    assert ctx.detail == "42"


def test_reply_chain_uses_source_trader_id_when_resolved_is_none(resolver, channel_config, raw_repo):
    channel_config.lookup.return_value = _entry(None, aliases={})
    raw_repo.get_chain_node.return_value = ChainNode(
        source_trader_id="trader_c",
        resolved_trader_id=None,
        raw_text="old signal",
        reply_to_message_id=None,
    )
    ctx = resolver.resolve(_envelope(text="close", reply_id=55))
    assert ctx.trader_id == "trader_c"
    assert ctx.method == "reply_chain"


def test_reply_chain_parent_not_in_db_returns_unresolved(resolver, channel_config, raw_repo):
    channel_config.lookup.return_value = _entry(None, aliases={})
    raw_repo.get_chain_node.return_value = None
    ctx = resolver.resolve(_envelope(text="close", reply_id=55))
    assert ctx.trader_id is None
    assert ctx.method == "unresolved"


def test_reply_chain_walks_to_grandparent(resolver, channel_config, raw_repo):
    channel_config.lookup.return_value = _entry(None, aliases={})
    raw_repo.get_chain_node.side_effect = [
        ChainNode(source_trader_id=None, resolved_trader_id=None, raw_text="update", reply_to_message_id=10),
        ChainNode(source_trader_id=None, resolved_trader_id="trader_d", raw_text="signal", reply_to_message_id=None),
    ]
    ctx = resolver.resolve(_envelope(text="close", reply_id=20))
    assert ctx.trader_id == "trader_d"
    assert ctx.method == "reply_chain"
    assert ctx.detail == "10"


def test_reply_chain_alias_in_parent_text(resolver, channel_config, raw_repo):
    channel_config.lookup.return_value = _entry(None, aliases={"trader#a": "trader_a"})
    raw_repo.get_chain_node.return_value = ChainNode(
        source_trader_id=None,
        resolved_trader_id=None,
        raw_text="Trader #A buy btc",
        reply_to_message_id=None,
    )
    ctx = resolver.resolve(_envelope(text="sl moved", reply_id=42))
    assert ctx.trader_id == "trader_a"
    assert ctx.method == "reply_chain_alias"


def test_reply_chain_respects_max_depth(resolver, channel_config, raw_repo):
    channel_config.lookup.return_value = _entry(None, aliases={}, max_depth=2)
    # Catena di 5 nodi, tutti unresolved — deve fermarsi a depth=2
    raw_repo.get_chain_node.side_effect = [
        ChainNode(None, None, "msg", reply_to_message_id=i)
        for i in range(10, 5, -1)
    ]
    ctx = resolver.resolve(_envelope(text="close", reply_id=15))
    assert ctx.trader_id is None
    assert ctx.method == "unresolved"
    assert raw_repo.get_chain_node.call_count == 2


# --- Step 4: link singolo ---

def test_single_link_resolved_via_chain(resolver, channel_config, raw_repo):
    channel_config.lookup.return_value = _entry(None, aliases={})
    raw_repo.get_chain_node.return_value = ChainNode(
        source_trader_id=None,
        resolved_trader_id="trader_e",
        raw_text="signal",
        reply_to_message_id=None,
    )
    ctx = resolver.resolve(_envelope(text="see https://t.me/c/12345678/99"))
    assert ctx.trader_id == "trader_e"
    assert ctx.method == "link"


# --- Step 5: link multipli ---

def test_multi_link_concordant(resolver, channel_config, raw_repo):
    channel_config.lookup.return_value = _entry(None, aliases={})
    raw_repo.get_chain_node.return_value = ChainNode(
        source_trader_id=None,
        resolved_trader_id="trader_f",
        raw_text="signal",
        reply_to_message_id=None,
    )
    ctx = resolver.resolve(_envelope(text="https://t.me/c/1/10 and https://t.me/c/1/20"))
    assert ctx.trader_id == "trader_f"
    assert ctx.method == "link_multi"


def test_multi_link_discordant_ambiguous(resolver, channel_config, raw_repo):
    channel_config.lookup.return_value = _entry(None, aliases={})
    raw_repo.get_chain_node.side_effect = [
        ChainNode(None, "trader_a", "sig", None),
        ChainNode(None, "trader_b", "sig", None),
    ]
    ctx = resolver.resolve(_envelope(text="https://t.me/c/1/10 and https://t.me/c/1/20"))
    assert ctx.trader_id is None
    assert ctx.is_ambiguous is True
    assert ctx.method == "content_alias_ambiguous"


# --- Tag vince su reply ---

def test_text_tag_wins_over_reply_chain(resolver, channel_config, raw_repo):
    channel_config.lookup.return_value = _entry(None, aliases={"trader#a": "trader_a"})
    raw_repo.get_chain_node.return_value = ChainNode(None, "trader_b", "signal", None)
    ctx = resolver.resolve(_envelope(text="Trader #A update", reply_id=42))
    assert ctx.trader_id == "trader_a"
    assert ctx.method == "content_alias"
    raw_repo.get_chain_node.assert_not_called()


# --- Unresolved ---

def test_no_signal_unresolved(resolver, channel_config, raw_repo):
    channel_config.lookup.return_value = _entry(None, aliases={})
    raw_repo.get_chain_node.return_value = None
    ctx = resolver.resolve(_envelope(text="ciao come va"))
    assert ctx.trader_id is None
    assert ctx.method == "unresolved"
    assert not ctx.is_ambiguous
```

- [ ] **Step 2: Esegui i test per verificare che falliscano**

```
pytest tests/telegram/test_trader_resolver.py -v
```
Expected: FAIL con `ModuleNotFoundError: No module named 'src.telegram.trader_resolver'`

- [ ] **Step 3: Crea `src/telegram/trader_resolver.py`**

```python
"""Single-entry trader resolution for all channel types.

Priority order:
  1. Config static (channels.yaml trader_id)         → source_chat_id / source_topic_config
  2. Alias in current message text (per-topic)        → content_alias
  3. Pattern extractors (hardcoded fallback)          → content_alias
  4. Reply chain (reply_to_message_id)                → reply_chain / reply_chain_alias
  5. Single t.me link in text                         → link
  6. Multiple t.me links — concordant/discordant      → link_multi / content_alias_ambiguous
  7. No signal                                        → unresolved
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from src.core.trader_tags import find_normalized_trader_tags
from src.runtime_v2.intake.models import RawMessageEnvelope
from src.runtime_v2.persistence.raw_messages import RawMessageRepository
from src.runtime_v2.trader_resolution.channel_config_resolver import ChannelConfigResolver
from src.runtime_v2.trader_resolution.models import ResolvedTraderContext
from src.telegram.pattern_extractors import extract_trader_by_pattern

_TELEGRAM_LINK_RE = re.compile(
    r"(?:https?://)?t\.me/(?:c/\d+|[A-Za-z0-9_]+)/(\d+)",
    re.IGNORECASE,
)


class TraderResolver:
    def __init__(
        self,
        channel_config: ChannelConfigResolver,
        raw_repo: RawMessageRepository,
    ) -> None:
        self._config = channel_config
        self._raw_repo = raw_repo

    def resolve(self, envelope: RawMessageEnvelope) -> ResolvedTraderContext:
        now = datetime.now(timezone.utc)

        # Step 1: config statico
        entry = self._config.lookup(envelope.source_chat_id, envelope.source_topic_id)
        if entry is not None and entry.active and entry.trader_id:
            method = (
                "source_topic_config"
                if envelope.source_topic_id is not None and entry.topic_id is not None
                else "source_chat_id"
            )
            return ResolvedTraderContext(
                raw_message_id=envelope.raw_message_id,
                trader_id=entry.trader_id,
                method=method,
                detail=None,
                is_ambiguous=False,
                resolved_at=now,
            )

        aliases = entry.aliases if entry is not None else {}
        max_depth = entry.resolution_max_depth if entry is not None else 5
        topic_id = envelope.source_topic_id

        # Step 2: alias + pattern nel testo corrente
        trader_id, is_ambiguous = self._from_text(envelope.raw_text, aliases, topic_id)
        if is_ambiguous:
            return ResolvedTraderContext(
                raw_message_id=envelope.raw_message_id,
                trader_id=None,
                method="content_alias_ambiguous",
                detail=None,
                is_ambiguous=True,
                resolved_at=now,
            )
        if trader_id is not None:
            return ResolvedTraderContext(
                raw_message_id=envelope.raw_message_id,
                trader_id=trader_id,
                method="content_alias",
                detail=None,
                is_ambiguous=False,
                resolved_at=now,
            )

        # Step 3-4: reply chain
        if envelope.reply_to_message_id is not None:
            chain = self._resolve_chain(
                envelope.source_chat_id, envelope.reply_to_message_id,
                aliases, topic_id, max_depth,
            )
            if chain is not None:
                return ResolvedTraderContext(
                    raw_message_id=envelope.raw_message_id,
                    trader_id=chain[0],
                    method=chain[1],
                    detail=chain[2],
                    is_ambiguous=False,
                    resolved_at=now,
                )

        # Step 5-6: link nel testo
        links = _extract_links(envelope.raw_text)
        if len(links) == 1:
            chain = self._resolve_chain(
                envelope.source_chat_id, links[0], aliases, topic_id, max_depth,
            )
            if chain is not None:
                return ResolvedTraderContext(
                    raw_message_id=envelope.raw_message_id,
                    trader_id=chain[0],
                    method="link",
                    detail=chain[2],
                    is_ambiguous=False,
                    resolved_at=now,
                )
        elif len(links) > 1:
            traders: set[str] = set()
            for link_msg_id in links:
                chain = self._resolve_chain(
                    envelope.source_chat_id, link_msg_id, aliases, topic_id, max_depth,
                )
                if chain is not None:
                    traders.add(chain[0])
            if len(traders) == 1:
                return ResolvedTraderContext(
                    raw_message_id=envelope.raw_message_id,
                    trader_id=traders.pop(),
                    method="link_multi",
                    detail=None,
                    is_ambiguous=False,
                    resolved_at=now,
                )
            if len(traders) > 1:
                return ResolvedTraderContext(
                    raw_message_id=envelope.raw_message_id,
                    trader_id=None,
                    method="content_alias_ambiguous",
                    detail=None,
                    is_ambiguous=True,
                    resolved_at=now,
                )

        return ResolvedTraderContext(
            raw_message_id=envelope.raw_message_id,
            trader_id=None,
            method="unresolved",
            detail=None,
            is_ambiguous=False,
            resolved_at=now,
        )

    def _from_text(
        self,
        raw_text: str | None,
        aliases: dict[str, str],
        topic_id: int | None,
    ) -> tuple[str | None, bool]:
        """Returns (trader_id, is_ambiguous). None+False means no match."""
        if not raw_text:
            return None, False
        if aliases:
            tags = find_normalized_trader_tags(raw_text)
            found = {aliases[tag] for tag in tags if tag in aliases}
            if len(found) == 1:
                return found.pop(), False
            if len(found) > 1:
                return None, True
        if topic_id is not None:
            pattern_result = extract_trader_by_pattern(topic_id, raw_text)
            if pattern_result is not None:
                return pattern_result, False
        return None, False

    def _resolve_chain(
        self,
        source_chat_id: str,
        start_msg_id: int,
        aliases: dict[str, str],
        topic_id: int | None,
        max_depth: int,
    ) -> tuple[str, str, str] | None:
        """Returns (trader_id, method, detail) or None if not resolved."""
        visited: set[int] = set()
        current_id: int | None = start_msg_id
        depth = 0

        while current_id is not None and depth < max_depth:
            if current_id in visited:
                break
            visited.add(current_id)

            node = self._raw_repo.get_chain_node(source_chat_id, current_id)
            if node is None:
                break

            resolved = node.resolved_trader_id or node.source_trader_id
            if resolved:
                return resolved, "reply_chain", str(current_id)

            text_trader, _ = self._from_text(node.raw_text, aliases, topic_id)
            if text_trader:
                return text_trader, "reply_chain_alias", str(current_id)

            current_id = node.reply_to_message_id
            depth += 1

        return None


def _extract_links(raw_text: str | None) -> list[int]:
    if not raw_text:
        return []
    return [int(m.group(1)) for m in _TELEGRAM_LINK_RE.finditer(raw_text)]
```

- [ ] **Step 4: Esegui i test**

```
pytest tests/telegram/test_trader_resolver.py -v
```
Expected: tutti PASS

- [ ] **Step 5: Commit**

```
git add src/telegram/trader_resolver.py tests/telegram/test_trader_resolver.py
git commit -m "feat: add TraderResolver with full priority cascade"
```

---

## Task 6: Wire `TraderResolver` in `listener._process_item()`

**Files:**
- Modify: `src/telegram/listener.py`

- [ ] **Step 1: Aggiungi `TraderResolver` al costruttore di `TelegramListener`**

In `listener.py`, aggiungi l'import:
```python
from src.telegram.trader_resolver import TraderResolver
```

Aggiungi il parametro al costruttore (dopo `enrichment_processor`):
```python
trader_resolver: TraderResolver,
```

E salva come attributo in `__init__`:
```python
self._trader_resolver = trader_resolver
```

- [ ] **Step 2: Aggiorna `_process_item()` per usare `TraderResolver`**

Sostituisci il corpo di `_process_item()` (righe 401-465) con:

```python
def _process_item(self, item: _QueueItem) -> None:
    entry = self._channel_resolver.lookup(item.source_chat_id, item.source_topic_id)
    if entry is None or not entry.active:
        self._logger.debug(
            "no active channel entry | raw_message_id=%s chat=%s topic=%s",
            item.raw_message_id,
            item.source_chat_id,
            item.source_topic_id,
        )
        return

    envelope = self._raw_repo.get_by_id(item.raw_message_id)

    resolved = self._trader_resolver.resolve(envelope)
    resolved = resolved.model_copy(update={"raw_message_id": item.raw_message_id})

    if resolved.is_ambiguous or resolved.trader_id is None:
        self._logger.info(
            "trader unresolved | raw_message_id=%s method=%s",
            item.raw_message_id,
            resolved.method,
        )
        self._raw_repo.update_processing_status(item.raw_message_id, "review")
        return

    self._raw_repo.update_trader_resolution(item.raw_message_id, resolved)

    parser_profile = entry.parser_profile if entry.parser_profile else resolved.trader_id

    raw_context = RawContext(
        raw_text=envelope.raw_text or "",
        message_id=envelope.telegram_message_id,
        reply_to_message_id=envelope.reply_to_message_id,
        source_chat_id=envelope.source_chat_id,
        source_topic_id=envelope.source_topic_id,
    )
    parser_context = ParserContext(
        raw_context=raw_context,
        message_id=envelope.telegram_message_id,
        reply_to_message_id=envelope.reply_to_message_id,
        source_chat_id=envelope.source_chat_id,
        source_topic_id=envelope.source_topic_id,
    )
    candidate = ParserDispatchCandidate(
        raw_message=envelope,
        resolved_trader=resolved,
        parser_profile=parser_profile,
        parser_context=parser_context,
    )

    result = self._parser_pipeline.process(candidate)
    if isinstance(result, ParserJobStatus):
        self._logger.warning(
            "parse failed | raw_message_id=%s reason=%s",
            item.raw_message_id,
            result.reason,
        )
    else:
        self._logger.info(
            "parsed | raw_message_id=%s canonical_id=%s class=%s status=%s trader=%s",
            item.raw_message_id,
            result.canonical_message_id,
            result.primary_class,
            result.parse_status,
            resolved.trader_id,
        )
        enriched = self._enrichment_processor.process(result)
        self._logger.info(
            "enriched | canonical_id=%s decision=%s reason=%s lifecycle_processed=%s",
            enriched.canonical_message_id,
            enriched.enrichment_decision,
            enriched.reason_code,
            enriched.lifecycle_processed,
        )
```

- [ ] **Step 3: Esegui i test esistenti del listener**

```
pytest tests/runtime_v2/ -k "listener or process_item" -v
```
Expected: PASS (o errori di costruttore — risolvere aggiungendo `trader_resolver` ai fixture)

- [ ] **Step 4: Esegui la suite completa**

```
pytest tests/ -x -q
```
Expected: tutti PASS

- [ ] **Step 5: Commit**

```
git add src/telegram/listener.py
git commit -m "feat: wire TraderResolver into listener._process_item, write resolved_trader_id to DB"
```

---

## Task 7: Aggiorna `channels.yaml`

**Files:**
- Modify: `config/channels.yaml`

- [ ] **Step 1: Aggiungi blocco `resolution` per topic multi-trader reali**

Per ogni topic che ha `trader_id: null` (multi-trader), aggiungi il blocco `resolution` con gli alias effettivi. Esempio (adattare al canale reale):

```yaml
  - chat_id: -1003722628653
    topic_id: 9           # adattare al topic_id reale del canale multi-trader RSI
    label: "RSI_MultiTrader"
    active: false         # attivare quando pronto
    trader_id: null
    parser_profile: null
    resolution:
      max_depth: 5
      aliases: {}         # compilare con i tag reali usati dai trader
    blacklist: []
```

Per i topic esistenti con `trader_id` già dichiarato non serve nulla — funzionano già.

- [ ] **Step 2: Verifica che il config si carichi senza errori**

```python
python -c "
from src.runtime_v2.trader_resolution.channel_config_resolver import ChannelConfigResolver
c = ChannelConfigResolver('config/channels.yaml')
print('OK', len(list(c._index)))
"
```
Expected: `OK N` senza errori

- [ ] **Step 3: Commit**

```
git add config/channels.yaml
git commit -m "config: add resolution block for multi-trader topics"
```

---

## Task 8: Rimuovi dead code

**Files:**
- Modify: `src/telegram/effective_trader.py`
- Modify: `src/runtime_v2/trader_resolution/resolver.py`
- Delete: `config/telegram_source_map.json`

- [ ] **Step 1: Aggiungi deprecation warning in `effective_trader.py`**

Aggiungi in cima al file, dopo gli import:
```python
import warnings
warnings.warn(
    "effective_trader.EffectiveTraderResolver is deprecated. Use TraderResolver instead.",
    DeprecationWarning,
    stacklevel=2,
)
```

- [ ] **Step 2: Aggiungi deprecation warning in `resolver.py`**

Aggiungi in cima al file, dopo gli import:
```python
import warnings
warnings.warn(
    "RuntimeV2TraderResolver is deprecated. Use TraderResolver instead.",
    DeprecationWarning,
    stacklevel=2,
)
```

- [ ] **Step 3: Rimuovi `telegram_source_map.json`**

```
git rm config/telegram_source_map.json
```

- [ ] **Step 4: Esegui la suite completa per verificare nessuna regressione**

```
pytest tests/ -x -q
```
Expected: tutti PASS

- [ ] **Step 5: Commit**

```
git add src/telegram/effective_trader.py src/runtime_v2/trader_resolution/resolver.py
git commit -m "deprecate: EffectiveTraderResolver and RuntimeV2TraderResolver replaced by TraderResolver; remove telegram_source_map.json"
```

---

## Self-Review

**Copertura spec:**
- ✅ Config statico → Task 1, Task 6
- ✅ Alias per-topic → Task 1, Task 5
- ✅ Pattern extractor hardcoded → Task 4, Task 5
- ✅ Reply chain con `resolved_trader_id ?? source_trader_id` → Task 2, Task 5
- ✅ Stop al primo parent risolto → Task 5 (`_resolve_chain`)
- ✅ Max depth configurabile → Task 1, Task 5
- ✅ Tag vince su reply → Task 5 (step 2 prima di step 3)
- ✅ Link singolo = logica reply → Task 5
- ✅ Link multipli concordi/discordanti → Task 5
- ✅ `parser_profile: null` → usa `resolved_trader_id` → Task 6
- ✅ `resolved_trader_id` scritto dopo risoluzione → Task 6
- ✅ Unresolved → review → Task 6
- ✅ `telegram_source_map.json` rimosso → Task 8
- ✅ Dead code deprecato → Task 8
