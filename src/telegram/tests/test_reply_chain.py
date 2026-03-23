"""Tests for transitive reply-chain trader resolution in EffectiveTraderResolver."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.storage.raw_messages import StoredRawMessage
from src.telegram.effective_trader import (
    _MAX_REPLY_CHAIN_DEPTH,
    EffectiveTraderContext,
    EffectiveTraderResolver,
)


def _stored(
    *,
    msg_id: int,
    source_trader_id: str | None = None,
    raw_text: str | None = None,
    reply_to: int | None = None,
) -> StoredRawMessage:
    return StoredRawMessage(
        raw_message_id=msg_id,
        source_chat_id="-100chat",
        telegram_message_id=msg_id,
        source_trader_id=source_trader_id,
        raw_text=raw_text,
        reply_to_message_id=reply_to,
    )


def _resolver(db_map: dict[int, StoredRawMessage]) -> EffectiveTraderResolver:
    """Build resolver with a fake raw_store backed by db_map."""
    source_mapper = MagicMock()
    source_mapper.resolve.return_value = MagicMock(trader_id=None)

    raw_store = MagicMock()
    raw_store.get_by_source_and_message_id.side_effect = (
        lambda source_chat_id, telegram_message_id: db_map.get(telegram_message_id)
    )

    return EffectiveTraderResolver(
        source_mapper=source_mapper,
        raw_store=raw_store,
        trader_aliases={"trader#a": "trader_a", "trader#b": "trader_b"},
        known_trader_ids={"trader_a", "trader_b"},
    )


def _ctx(reply_to: int | None = None, raw_text: str = "short update") -> EffectiveTraderContext:
    return EffectiveTraderContext(
        source_chat_id="-100chat",
        source_chat_username=None,
        source_chat_title=None,
        raw_text=raw_text,
        reply_to_message_id=reply_to,
    )


# ---------------------------------------------------------------------------
# 1. Catena semplice: child → parent diretto con source_trader_id
# ---------------------------------------------------------------------------

def test_chain_direct_parent_source_trader_id() -> None:
    """Compatibilità con il comportamento pre-esistente: parent diretto ha trader."""
    db = {
        10: _stored(msg_id=10, source_trader_id="trader_a"),
    }
    resolver = _resolver(db)
    result = resolver.resolve(_ctx(reply_to=10))
    assert result.trader_id == "trader_a"
    assert result.method == "reply_chain"
    assert result.detail == "10"


# ---------------------------------------------------------------------------
# 2. Catena a due livelli: child → parent (no trader) → nonno (ha trader)
# ---------------------------------------------------------------------------

def test_chain_two_levels_source_trader_id() -> None:
    """reply → parent senza trader → grandparent con source_trader_id."""
    db = {
        10: _stored(msg_id=10, source_trader_id=None, reply_to=5),
        5: _stored(msg_id=5, source_trader_id="trader_a"),
    }
    resolver = _resolver(db)
    result = resolver.resolve(_ctx(reply_to=10))
    assert result.trader_id == "trader_a"
    assert result.method == "reply_chain"
    assert result.detail == "5"


# ---------------------------------------------------------------------------
# 3. Alias nel testo del parent (no source_trader_id)
# ---------------------------------------------------------------------------

def test_chain_alias_in_parent_text() -> None:
    """Trova il trader dal tag nel raw_text del parent."""
    db = {
        10: _stored(msg_id=10, source_trader_id=None, raw_text="Trader #A: BTC long"),
    }
    resolver = _resolver(db)
    result = resolver.resolve(_ctx(reply_to=10))
    assert result.trader_id == "trader_a"
    assert result.method == "reply_chain_alias"
    assert result.detail == "10"


# ---------------------------------------------------------------------------
# 4. Alias nel nonno (due livelli) — source_trader_id prioritario sul testo
# ---------------------------------------------------------------------------

def test_chain_alias_at_grandparent_level() -> None:
    """Alias trovato al livello 2, dopo parent senza info."""
    db = {
        20: _stored(msg_id=20, source_trader_id=None, raw_text="update", reply_to=10),
        10: _stored(msg_id=10, source_trader_id=None, raw_text="Trader #B entry", reply_to=None),
    }
    resolver = _resolver(db)
    result = resolver.resolve(_ctx(reply_to=20))
    assert result.trader_id == "trader_b"
    assert result.method == "reply_chain_alias"
    assert result.detail == "10"


# ---------------------------------------------------------------------------
# 5. source_trader_id prioritario rispetto all'alias nel testo
# ---------------------------------------------------------------------------

def test_chain_source_trader_id_wins_over_alias() -> None:
    """Se il parent ha sia source_trader_id che un alias diverso nel testo,
    source_trader_id vince perché controllato prima."""
    db = {
        10: _stored(
            msg_id=10,
            source_trader_id="trader_a",
            raw_text="Trader #B segnale",  # alias diverso, non deve vincere
        ),
    }
    resolver = _resolver(db)
    result = resolver.resolve(_ctx(reply_to=10))
    assert result.trader_id == "trader_a"
    assert result.method == "reply_chain"


# ---------------------------------------------------------------------------
# 6. Catena che finisce senza trovare trader (parent mancante in DB)
# ---------------------------------------------------------------------------

def test_chain_parent_not_in_db() -> None:
    """Il parent punta a un messaggio non presente in DB → unresolved."""
    db: dict[int, StoredRawMessage] = {}  # DB vuoto
    source_mapper = MagicMock()
    source_mapper.resolve.return_value = MagicMock(trader_id=None)
    raw_store = MagicMock()
    raw_store.get_by_source_and_message_id.return_value = None

    resolver = EffectiveTraderResolver(
        source_mapper=source_mapper,
        raw_store=raw_store,
        trader_aliases={},
        known_trader_ids=set(),
    )
    result = resolver.resolve(_ctx(reply_to=99))
    assert result.trader_id is None


# ---------------------------------------------------------------------------
# 7. Protezione da loop (A reply-to B reply-to A)
# ---------------------------------------------------------------------------

def test_chain_loop_protection() -> None:
    """Catena con ciclo: 10 → 20 → 10. Deve terminare senza exception."""
    db = {
        10: _stored(msg_id=10, source_trader_id=None, reply_to=20),
        20: _stored(msg_id=20, source_trader_id=None, reply_to=10),  # loop
    }
    resolver = _resolver(db)
    result = resolver.resolve(_ctx(reply_to=10))
    # Non deve lanciare. Trader non trovato = unresolved.
    assert result.trader_id is None


# ---------------------------------------------------------------------------
# 8. Catena che supera il limite di profondità massima
# ---------------------------------------------------------------------------

def test_chain_max_depth_stops() -> None:
    """Catena di lunghezza > MAX_REPLY_CHAIN_DEPTH: si ferma senza loop."""
    # Costruisce una catena lineare lunga MAX+5: 100 → 99 → ... → 1 (trader in fondo)
    chain_length = _MAX_REPLY_CHAIN_DEPTH + 5
    db: dict[int, StoredRawMessage] = {}
    for i in range(1, chain_length + 1):
        parent = i - 1 if i > 1 else None
        source_trader_id = "trader_a" if i == 1 else None
        db[i] = _stored(msg_id=i, source_trader_id=source_trader_id, reply_to=parent)

    resolver = _resolver(db)
    # Partiamo dalla fine della catena (msg_id = chain_length)
    result = resolver.resolve(_ctx(reply_to=chain_length))
    # Il trader è a profondità chain_length - 1 > MAX_REPLY_CHAIN_DEPTH → non trovato
    assert result.trader_id is None


# ---------------------------------------------------------------------------
# 9. Alias nel testo del messaggio corrente ha priorità sulla catena reply
# ---------------------------------------------------------------------------

def test_content_alias_takes_priority_over_chain() -> None:
    """Il tag nel testo del messaggio corrente ha priorità su tutta la reply chain."""
    db = {
        10: _stored(msg_id=10, source_trader_id="trader_b"),
    }
    resolver = _resolver(db)
    # Il testo del messaggio corrente contiene "Trader #A"
    result = resolver.resolve(_ctx(reply_to=10, raw_text="Trader #A stop hit"))
    assert result.trader_id == "trader_a"
    assert result.method == "content_alias"


# ---------------------------------------------------------------------------
# 10. Nessuna reply e nessun alias → unresolved
# ---------------------------------------------------------------------------

def test_no_reply_no_alias_unresolved() -> None:
    resolver = _resolver({})
    result = resolver.resolve(_ctx(reply_to=None, raw_text="no tags here"))
    assert result.trader_id is None
    assert result.method == "unresolved"
