# Control Plane — delivery_mode Delta Plan

> **For agentic workers:** Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Estendere il Control Plane con `delivery_mode: supergroup_topics | private_bot`. La modalità `supergroup_topics` rimane invariata. La modalità `private_bot` supporta chat privata senza thread_id, Reply Keyboard, e prefisso `⚠️ --SYSTEM--` sui messaggi TECH_LOG.

**Source spec:** `docs/superpowers/specs/2026-05-30-control-plane-delivery-mode-design.md`

**Quando eseguire:** Questo piano si esegue in parallelo ai piani originali delle parti. Ogni task indica dopo quale parte eseguirlo.

---

## Task 1 — Aggiorna Parte 1 già implementata

**Dopo:** Parte 1 completata (già fatto).
**Files:** `src/runtime_v2/control_plane/models.py`, `config.py`, `auth.py`, test relativi.

### Step 1: Aggiorna `models.py` — thread_id opzionale + delivery_mode

- [ ] In `TopicConfig`: cambia `thread_id: int` → `thread_id: int | None = None`
- [ ] In `TechLogConfig`: stesso cambio (eredita da TopicConfig, verificare se ridefinisce thread_id)
- [ ] In `CleanLogConfig`: stesso cambio
- [ ] In `ControlPlaneConfig`: aggiungi campo

```python
delivery_mode: Literal["supergroup_topics", "private_bot"] = "supergroup_topics"
```

- [ ] Aggiorna `__all__` se necessario
- [ ] Esegui: `python -m pytest tests/runtime_v2/control_plane/test_models.py -v` — deve passare (nessuna regressione)

### Step 2: Aggiorna `config.py` — topics opzionale in private_bot

- [ ] Nel metodo `load_control_plane_config`, dopo `_substitute_env(raw)` e prima di `ControlPlaneConfig.model_validate(raw)`, aggiungi:

```python
if raw.get("delivery_mode") == "private_bot" and "topics" not in raw:
    raw["topics"] = {
        "commands": {"thread_id": None},
        "tech_log":  {"thread_id": None},
        "clean_log": {"thread_id": None},
    }
```

- [ ] Esegui: `python -m pytest tests/runtime_v2/control_plane/test_config.py -v` — deve passare

### Step 3: Aggiorna `auth.py` — branch private_bot

- [ ] In `AuthValidator.__init__`: aggiungi `self._delivery_mode = config.delivery_mode`
- [ ] In `validate()`: sostituisci il check thread_id con:

```python
if self._delivery_mode == "supergroup_topics":
    if thread_id != self._commands_thread_id:
        return AuthResult("IGNORE", "wrong_topic")
```

- [ ] Esegui: `python -m pytest tests/runtime_v2/control_plane/test_auth.py -v` — deve passare

### Step 4: Aggiungi nuovi test

- [ ] In `tests/runtime_v2/control_plane/test_auth.py`, aggiungi:

```python
def _config_private_bot() -> ControlPlaneConfig:
    return ControlPlaneConfig(
        token="t",
        chat_id=-100999,
        delivery_mode="private_bot",
        topics=TopicsConfig(
            commands=TopicConfig(thread_id=None),
            tech_log=TechLogConfig(thread_id=None),
            clean_log=CleanLogConfig(thread_id=None),
        ),
        authorized_users=[42, 43],
    )


def test_private_bot_authorized_no_thread():
    v = AuthValidator(_config_private_bot())
    res = v.validate(chat_id=-100999, thread_id=None, user_id=42)
    assert res.decision == "OK"


def test_private_bot_wrong_chat():
    v = AuthValidator(_config_private_bot())
    res = v.validate(chat_id=-1, thread_id=None, user_id=42)
    assert res.decision == "IGNORE"
    assert res.reason == "wrong_chat"


def test_private_bot_unauthorized_user():
    v = AuthValidator(_config_private_bot())
    res = v.validate(chat_id=-100999, thread_id=None, user_id=99)
    assert res.decision == "REJECT_UNAUTHORIZED"
```

- [ ] In `tests/runtime_v2/control_plane/test_config.py`, aggiungi:

```python
_PRIVATE_BOT_YAML = """
delivery_mode: private_bot
token_env: CP_TOKEN
chat_id: "${CP_CHAT}"
authorized_users:
  - "${CP_USER}"
"""


def test_private_bot_config_without_topics(tmp_path, monkeypatch):
    monkeypatch.setenv("CP_TOKEN", "999:XYZ")
    monkeypatch.setenv("CP_CHAT", "-1009999")
    monkeypatch.setenv("CP_USER", "42")
    cfg = load_control_plane_config(_write(tmp_path, _PRIVATE_BOT_YAML))
    assert cfg.delivery_mode == "private_bot"
    assert cfg.topics.commands.thread_id is None
    assert cfg.topics.tech_log.thread_id is None
    assert cfg.topics.clean_log.thread_id is None


def test_supergroup_without_topics_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("CP_TOKEN", "999:XYZ")
    monkeypatch.setenv("CP_CHAT", "-1009999")
    monkeypatch.setenv("CP_USER", "42")
    bad = """
delivery_mode: supergroup_topics
token_env: CP_TOKEN
chat_id: "${CP_CHAT}"
authorized_users:
  - "${CP_USER}"
"""
    with pytest.raises(ControlPlaneConfigError):
        load_control_plane_config(_write(tmp_path, bad))
```

### Step 5: Run suite completa Parte 1

- [ ] Esegui: `python -m pytest tests/runtime_v2/control_plane/ -v` — tutti verdi

### Step 6: Commit

```bash
git add src/runtime_v2/control_plane/models.py \
        src/runtime_v2/control_plane/config.py \
        src/runtime_v2/control_plane/auth.py \
        tests/runtime_v2/control_plane/test_auth.py \
        tests/runtime_v2/control_plane/test_config.py
git commit -m "feat(control_plane): add delivery_mode supergroup_topics|private_bot

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Task 2 — topic_router.py (Parte 2)

**Dopo:** Parte 2, step che crea `topic_router.py`.
**File:** `src/runtime_v2/control_plane/topic_router.py`

Quando scrivi `topic_router.py` nella Parte 2, implementalo già con il branching `delivery_mode`:

```python
# src/runtime_v2/control_plane/topic_router.py
from __future__ import annotations

from src.runtime_v2.control_plane.models import ControlPlaneConfig, Destination


class TopicRouter:
    def __init__(self, config: ControlPlaneConfig) -> None:
        self._chat_id = config.chat_id
        self._delivery_mode = config.delivery_mode
        self._thread_map: dict[str, int | None] = {
            "CLEAN_LOG":      config.topics.clean_log.thread_id,
            "TECH_LOG":       config.topics.tech_log.thread_id,
            "COMMANDS_REPLY": config.topics.commands.thread_id,
        }

    def route(self, destination: Destination) -> tuple[int, int | None]:
        """Restituisce (chat_id, thread_id). thread_id=None in private_bot."""
        if self._delivery_mode == "private_bot":
            return (self._chat_id, None)
        return (self._chat_id, self._thread_map[destination])


__all__ = ["TopicRouter"]
```

- [ ] Aggiungi test `topic_router` con fixture per entrambe le modalità:

```python
def test_supergroup_routes_to_thread():
    cfg = ...  # delivery_mode=supergroup_topics, thread_ids 101/102/103
    router = TopicRouter(cfg)
    assert router.route("CLEAN_LOG") == (-100999, 103)
    assert router.route("TECH_LOG") == (-100999, 102)
    assert router.route("COMMANDS_REPLY") == (-100999, 101)


def test_private_bot_routes_without_thread():
    cfg = ...  # delivery_mode=private_bot, thread_ids None
    router = TopicRouter(cfg)
    assert router.route("CLEAN_LOG") == (-100999, None)
    assert router.route("TECH_LOG") == (-100999, None)
    assert router.route("COMMANDS_REPLY") == (-100999, None)
```

---

## Task 3 — notification_dispatcher.py (Parte 2)

**Dopo:** Parte 2, step che crea `notification_dispatcher.py`.
**File:** `src/runtime_v2/control_plane/notification_dispatcher.py`

Quando chiami l'API Telegram nel dispatcher, usa il risultato di `topic_router.route()` senza assumere thread_id sempre presente:

```python
chat_id, thread_id = self._router.route(entry.destination)
kwargs: dict = {
    "chat_id": chat_id,
    "text": text,
    "parse_mode": "HTML",
}
if thread_id is not None:
    kwargs["message_thread_id"] = thread_id
await self._bot.send_message(**kwargs)
```

- [ ] Verifica che i test integration del dispatcher coprano entrambi i casi (con e senza thread_id)

---

## Task 4 — formatters/tech_log.py (Parte 5)

**Dopo:** Parte 5, step che crea `formatters/tech_log.py`.
**File:** `src/runtime_v2/control_plane/formatters/tech_log.py`

Il formatter riceve `delivery_mode` come parametro (oppure legge dalla config iniettata):

```python
def format_tech_log(entry, *, delivery_mode: str) -> str:
    body = _build_body(entry)
    if delivery_mode == "private_bot":
        return f"⚠️ --SYSTEM--\n{body}"
    return body
```

- [ ] Test: stessa entry → con `supergroup_topics` non ha prefisso; con `private_bot` inizia con `⚠️ --SYSTEM--\n`

---

## Task 5 — telegram_bot.py Reply Keyboard (Parte 3)

**Dopo:** Parte 3, step che registra gli handler in `telegram_bot.py`.
**File:** `src/runtime_v2/control_plane/telegram_bot.py`

In `private_bot`, invia la `ReplyKeyboardMarkup` al primo messaggio autorizzato o su `/start`:

```python
from telegram import ReplyKeyboardMarkup

async def _send_reply_keyboard(self, update: Update) -> None:
    if self._config.delivery_mode != "private_bot":
        return
    if not self._config.keyboard:
        return
    markup = ReplyKeyboardMarkup(
        self._config.keyboard,
        resize_keyboard=True,
        persistent=True,
    )
    await update.message.reply_text(".", reply_markup=markup)
```

- [ ] Chiama `_send_reply_keyboard` nell'handler `/start` e nell'handler catch-all al primo contatto autorizzato
- [ ] In `supergroup_topics` questo metodo è no-op (guardia sul `delivery_mode`)

---

## Checklist finale

- [ ] `python -m pytest tests/runtime_v2/control_plane/ -v` — tutti verdi
- [ ] Test manuale con YAML `private_bot`: bot risponde a `/status` in chat privata, keyboard visibile
- [ ] Test manuale con YAML `supergroup_topics`: comportamento invariato rispetto al design originale
- [ ] `grep -r "delivery_mode" src/runtime_v2/control_plane/` — presenti solo nei file attesi
