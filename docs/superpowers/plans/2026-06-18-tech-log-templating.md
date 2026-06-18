# Tech Log Templating System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Riscrivere `tech_log.py` con architettura a blocchi (block-based DSL), template per notification type, payload flat nei callsite, e fix del metodo mancante `write_command_failed_tech_log`.

**Architecture:** Infrastruttura `_blocks.py` e `_formatters.py` condivisa con clean_log. Un helper `_tech_header()` in `templates/tech_log.py` produce header uniformi senza toccare l'engine. Il dispatcher thin riceve `notification_type` e lo usa per selezionare il template dal registry.

**Tech Stack:** Python 3.12, pytest, SQLite (ops DB), struttura formatter esistente (`_blocks.py`, `_formatters.py`, `display.py`).

## Global Constraints

- Zero nuovi pacchetti — usare solo l'infrastruttura esistente.
- `_blocks.py` e `_formatters.py` restano invariati.
- `level` rimane nel payload come campo di filtering per `_should_send_tech_log` — non è usato nei template ma è letto dal dispatcher per policy gating.
- `category` e `title` vengono rimossi dai payload (erano solo per il formatter generico).
- Tutti i test esistenti (`test_tech_log_policy.py`, `test_clean_log_formatter.py`, ecc.) devono rimanere verdi.

---

### Task 1: Creare `templates/tech_log.py` con i 6 template

**Files:**
- Create: `src/runtime_v2/control_plane/formatters/templates/tech_log.py`
- Create: `tests/runtime_v2/control_plane/test_tech_log_formatter.py`

**Interfaces:**
- Consumes: `_blocks.py` (SeparatorBlock, DerivedBlock, FieldBlock, FooterBlock, TemplateConfig, render_template), `_formatters.py` (num, text), `display.py` (display_symbol)
- Produces: `TEMPLATE_REGISTRY: dict[str, TemplateConfig]` — usato da Task 2

- [ ] **Step 1: Scrivere i test failing**

```python
# tests/runtime_v2/control_plane/test_tech_log_formatter.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import render_template
from src.runtime_v2.control_plane.formatters.templates.tech_log import TEMPLATE_REGISTRY


def _render(notification_type: str, payload: dict) -> str:
    config = TEMPLATE_REGISTRY[notification_type]
    return render_template(config.blocks, payload, transform=config.payload_transform)


def test_runtime_startup_header_and_fields():
    text = _render("RUNTIME_STARTUP", {
        "started_at": "2026-06-18 10:00:00 UTC",
        "source": "runtime_main",
    })
    assert "ℹ️ RUNTIME: AVVIATO" in text
    assert "Started at: 2026-06-18 10:00:00 UTC" in text
    assert "Source: runtime_main" in text


def test_runtime_shutdown_all_fields():
    text = _render("RUNTIME_SHUTDOWN", {
        "reason": "SIGTERM",
        "open_chains": 3,
        "pending_commands": 1,
        "source": "runtime_main",
    })
    assert "ℹ️ RUNTIME: SHUTDOWN" in text
    assert "Reason: SIGTERM" in text
    assert "Open chains: 3" in text
    assert "Pending commands: 1" in text
    assert "Source: runtime_main" in text


def test_listener_edit_skipped_fields():
    text = _render("LISTENER_EDIT_SKIPPED", {
        "description": "Edit di un segnale con trade chain già creata — non riprocessato.",
        "chat": -100123,
        "msg_id": 789,
        "action": "verifica il messaggio",
        "source": "telegram_listener",
    })
    assert "⚠️ LISTENER: EDIT SKIPPED" in text
    assert "Chat: -100123" in text
    assert "Msg ID: 789" in text
    assert "Action: verifica il messaggio" in text
    assert "Source: telegram_listener" in text


def test_listener_edit_skipped_optional_edit_ts_absent():
    text = _render("LISTENER_EDIT_SKIPPED", {
        "chat": -100123,
        "msg_id": 789,
        "source": "telegram_listener",
    })
    assert "Edit ts" not in text
    assert "Action" not in text


def test_gateway_entry_all_failed_fields():
    text = _render("GATEWAY_ENTRY_ALL_FAILED", {
        "description": "Tutti i comandi PLACE_ENTRY falliti. Catena cancellata.",
        "chain_id": 42,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "reason": "order rejected by exchange",
        "action": "intervento manuale richiesto",
        "source": "execution_gateway",
    })
    assert "🛑 GATEWAY: ENTRY ALL FAILED" in text
    assert "#42" in text
    assert "BTC/USDT" in text
    assert "LONG" in text
    assert "order rejected by exchange" in text
    assert "intervento manuale richiesto" in text


def test_gateway_review_required_fields():
    text = _render("GATEWAY_REVIEW_REQUIRED", {
        "description": "Comando bloccato in REVIEW_REQUIRED.",
        "command_type": "PLACE_ENTRY",
        "chain_id": 42,
        "reason": "capability_missing:can_place_limit_entry",
        "action": "intervento manuale richiesto",
        "source": "execution_gateway",
    })
    assert "⚠️ GATEWAY: REVIEW REQUIRED" in text
    assert "Command: PLACE_ENTRY" in text
    assert "#42" in text
    assert "capability_missing" in text


def test_gateway_command_failed_fields():
    text = _render("GATEWAY_COMMAND_FAILED", {
        "command_type": "SET_SL",
        "chain_id": 42,
        "reason": "KeyError: 'order_id'",
        "source": "execution_gateway",
    })
    assert "🛑 GATEWAY: COMMAND FAILED" in text
    assert "Command: SET_SL" in text
    assert "#42" in text
    assert "KeyError" in text
    assert "Source: execution_gateway" in text


def test_gateway_command_failed_no_chain_id():
    text = _render("GATEWAY_COMMAND_FAILED", {
        "command_type": "SET_SL",
        "chain_id": None,
        "reason": "some error",
        "source": "execution_gateway",
    })
    assert "Chain" not in text
    assert "Reason: some error" in text


def test_all_six_types_are_registered():
    expected = {
        "RUNTIME_STARTUP",
        "RUNTIME_SHUTDOWN",
        "LISTENER_EDIT_SKIPPED",
        "GATEWAY_ENTRY_ALL_FAILED",
        "GATEWAY_REVIEW_REQUIRED",
        "GATEWAY_COMMAND_FAILED",
    }
    assert expected == set(TEMPLATE_REGISTRY.keys())
```

- [ ] **Step 2: Verificare che i test falliscano**

```
pytest tests/runtime_v2/control_plane/test_tech_log_formatter.py -v
```
Atteso: `ModuleNotFoundError` o `ImportError` — il file non esiste ancora.

- [ ] **Step 3: Creare `templates/tech_log.py`**

```python
# src/runtime_v2/control_plane/formatters/templates/tech_log.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import (
    SeparatorBlock, DerivedBlock,
    FieldBlock, FooterBlock,
    TemplateConfig,
)
from src.runtime_v2.control_plane.formatters._formatters import num, text
from src.runtime_v2.control_plane.formatters.display import display_symbol


def _tech_header(emoji: str, category: str, event_label: str) -> list:
    return [
        DerivedBlock(text_fn=lambda p, _e=emoji, _c=category, _l=event_label:
            f"{_e} {_c}: {_l}"),
        SeparatorBlock(),
    ]


_RUNTIME_STARTUP = TemplateConfig([
    *_tech_header("ℹ️", "RUNTIME", "AVVIATO"),
    FieldBlock("Started at", key="started_at", fmt=text, optional=False, default="n/a"),
    FooterBlock(default_source="runtime_main"),
])

_RUNTIME_SHUTDOWN = TemplateConfig([
    *_tech_header("ℹ️", "RUNTIME", "SHUTDOWN"),
    FieldBlock("Reason",           key="reason",           fmt=text, optional=False, default="n/a"),
    FieldBlock("Open chains",      key="open_chains",      fmt=num,  optional=False, default="n/a"),
    FieldBlock("Pending commands", key="pending_commands", fmt=num,  optional=False, default="n/a"),
    FooterBlock(default_source="runtime_main"),
])

_LISTENER_EDIT_SKIPPED = TemplateConfig([
    *_tech_header("⚠️", "LISTENER", "EDIT SKIPPED"),
    DerivedBlock(text_fn=lambda p: p.get("description") or ""),
    FieldBlock("Chat",    key="chat",    fmt=text),
    FieldBlock("Msg ID",  key="msg_id",  fmt=text),
    FieldBlock("Edit ts", key="edit_ts", fmt=text, optional=True),
    FieldBlock("Action",  key="action",  fmt=text, optional=True),
    FooterBlock(default_source="telegram_listener"),
])

_GATEWAY_ENTRY_ALL_FAILED = TemplateConfig([
    *_tech_header("🛑", "GATEWAY", "ENTRY ALL FAILED"),
    DerivedBlock(text_fn=lambda p: p.get("description") or ""),
    FieldBlock(
        "Chain",
        value_fn=lambda p: f"#{p['chain_id']}" if p.get("chain_id") is not None else None,
        fmt=text, optional=True,
    ),
    FieldBlock("Symbol", key="symbol", fmt=display_symbol, optional=True),
    FieldBlock("Side",   key="side",   fmt=text, optional=True),
    FieldBlock("Reason", key="reason", fmt=text, optional=False, default="n/a"),
    FieldBlock("Action", key="action", fmt=text, optional=True),
    FooterBlock(default_source="execution_gateway"),
])

_GATEWAY_REVIEW_REQUIRED = TemplateConfig([
    *_tech_header("⚠️", "GATEWAY", "REVIEW REQUIRED"),
    DerivedBlock(text_fn=lambda p: p.get("description") or ""),
    FieldBlock("Command", key="command_type", fmt=text, optional=True),
    FieldBlock(
        "Chain",
        value_fn=lambda p: f"#{p['chain_id']}" if p.get("chain_id") is not None else None,
        fmt=text, optional=True,
    ),
    FieldBlock("Reason", key="reason", fmt=text, optional=False, default="n/a"),
    FieldBlock("Action", key="action", fmt=text, optional=True),
    FooterBlock(default_source="execution_gateway"),
])

_GATEWAY_COMMAND_FAILED = TemplateConfig([
    *_tech_header("🛑", "GATEWAY", "COMMAND FAILED"),
    FieldBlock("Command", key="command_type", fmt=text, optional=True),
    FieldBlock(
        "Chain",
        value_fn=lambda p: f"#{p['chain_id']}" if p.get("chain_id") is not None else None,
        fmt=text, optional=True,
    ),
    FieldBlock("Reason", key="reason", fmt=text, optional=False, default="n/a"),
    FooterBlock(default_source="execution_gateway"),
])


TEMPLATE_REGISTRY: dict[str, TemplateConfig] = {
    "RUNTIME_STARTUP":          _RUNTIME_STARTUP,
    "RUNTIME_SHUTDOWN":         _RUNTIME_SHUTDOWN,
    "LISTENER_EDIT_SKIPPED":    _LISTENER_EDIT_SKIPPED,
    "GATEWAY_ENTRY_ALL_FAILED": _GATEWAY_ENTRY_ALL_FAILED,
    "GATEWAY_REVIEW_REQUIRED":  _GATEWAY_REVIEW_REQUIRED,
    "GATEWAY_COMMAND_FAILED":   _GATEWAY_COMMAND_FAILED,
}
```

- [ ] **Step 4: Verificare che i test passino**

```
pytest tests/runtime_v2/control_plane/test_tech_log_formatter.py -v
```
Atteso: tutti i test PASS.

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/control_plane/formatters/templates/tech_log.py tests/runtime_v2/control_plane/test_tech_log_formatter.py
git commit -m "feat: add templates/tech_log.py with block-based templates for 6 notification types"
```

---

### Task 2: Riscrivere `tech_log.py` e aggiornare `notification_dispatcher.py`

**Files:**
- Modify: `src/runtime_v2/control_plane/formatters/tech_log.py` (riscrittura completa)
- Modify: `src/runtime_v2/control_plane/notification_dispatcher.py:314-315` (una riga)

**Interfaces:**
- Consumes: `TEMPLATE_REGISTRY` da Task 1, `render_template` da `_blocks.py`
- Produces: `format_tech_log(notification_type: str, payload: dict, *, delivery_mode: str) -> str`

- [ ] **Step 1: Scrivere test per il dispatcher thin**

Aggiungere in fondo a `tests/runtime_v2/control_plane/test_tech_log_formatter.py`:

```python
from src.runtime_v2.control_plane.formatters.tech_log import format_tech_log


def test_format_tech_log_dispatches_to_template():
    text = format_tech_log("RUNTIME_STARTUP", {
        "started_at": "2026-06-18 10:00:00 UTC",
        "source": "runtime_main",
    })
    assert "ℹ️ RUNTIME: AVVIATO" in text


def test_format_tech_log_private_bot_prepends_system():
    text = format_tech_log(
        "RUNTIME_STARTUP",
        {"started_at": "2026-06-18 10:00:00 UTC"},
        delivery_mode="private_bot",
    )
    assert text.startswith("⚠️ --SYSTEM--\n")
    assert "ℹ️ RUNTIME: AVVIATO" in text


def test_format_tech_log_unknown_type_fallback():
    text = format_tech_log("UNKNOWN_EVENT", {
        "level": "ERROR",
        "description": "qualcosa è andato storto",
    })
    assert "UNKNOWN_EVENT" in text
    assert "qualcosa è andato storto" in text


def test_format_tech_log_fallback_default_delivery_mode():
    text = format_tech_log("UNKNOWN_EVENT", {"level": "INFO", "description": "x"})
    assert not text.startswith("⚠️ --SYSTEM--")
```

- [ ] **Step 2: Verificare che i nuovi test falliscano**

```
pytest tests/runtime_v2/control_plane/test_tech_log_formatter.py::test_format_tech_log_dispatches_to_template -v
```
Atteso: FAIL — `format_tech_log` ha ancora la vecchia firma.

- [ ] **Step 3: Riscrivere `tech_log.py`**

Sostituire l'intero contenuto del file:

```python
# src/runtime_v2/control_plane/formatters/tech_log.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import render_template
from src.runtime_v2.control_plane.formatters.templates.tech_log import TEMPLATE_REGISTRY


def format_tech_log(
    notification_type: str,
    payload: dict,
    *,
    delivery_mode: str = "supergroup_topics",
) -> str:
    config = TEMPLATE_REGISTRY.get(notification_type)
    body = (
        render_template(config.blocks, payload, transform=config.payload_transform)
        if config
        else _fallback(notification_type, payload)
    )
    if delivery_mode == "private_bot":
        return f"⚠️ --SYSTEM--\n{body}"
    return body


def _fallback(notification_type: str, payload: dict) -> str:
    level = str(payload.get("level", "INFO")).upper()
    description = payload.get("description") or notification_type
    return f"[{level}] {notification_type}\n────────────────\n{description}"


__all__ = ["format_tech_log"]
```

- [ ] **Step 4: Aggiornare `notification_dispatcher.py`**

Trovare la riga 315 (o cercare `format_tech_log(payload`) e aggiungere `notification_type` come primo argomento:

```python
# prima
return format_tech_log(payload, delivery_mode=self._config.delivery_mode)

# dopo
return format_tech_log(notification_type, payload, delivery_mode=self._config.delivery_mode)
```

- [ ] **Step 5: Verificare tutti i test**

```
pytest tests/runtime_v2/control_plane/test_tech_log_formatter.py tests/runtime_v2/control_plane/test_tech_log_policy.py -v
```
Atteso: tutti i test PASS. I test di policy usano `notification_type="RUNTIME_EVENT"` che non è nel registry → va nel fallback → comportamento invariato.

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/control_plane/formatters/tech_log.py src/runtime_v2/control_plane/notification_dispatcher.py tests/runtime_v2/control_plane/test_tech_log_formatter.py
git commit -m "feat: rewrite tech_log.py as thin dispatcher with notification_type routing"
```

---

### Task 3: Aggiornare i callsite e implementare `write_command_failed_tech_log`

**Files:**
- Modify: `src/runtime_v2/control_plane/service.py` (2 funzioni)
- Modify: `src/runtime_v2/control_plane/outbox_writer.py` (1 funzione)
- Modify: `src/runtime_v2/execution_gateway/repositories.py` (2 funzioni + 1 metodo nuovo)

**Interfaces:**
- Consumes: `write_tech_log_event` da `outbox_writer.py`, `format_tech_log` da Task 2
- Produces: payload flat per tutti i 6 tipi, metodo `write_command_failed_tech_log` implementato

> **Nota:** non ci sono test dedicati per i callsite perché il contratto payload→output è già coperto da Task 1. La validazione qui è funzionale: i test di policy continuano a passare e non ci sono errori di tipo a runtime.

- [ ] **Step 1: Aggiornare `service.py` — `send_startup_notification`**

```python
# src/runtime_v2/control_plane/service.py — send_startup_notification
# Sostituire il payload dict:

# prima
payload={
    "level": "INFO",
    "category": "Runtime",
    "description": "Runtime avviato",
    "source": "runtime_main",
    "context": {
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    },
},

# dopo
payload={
    "level": "INFO",
    "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    "source": "runtime_main",
},
```

- [ ] **Step 2: Aggiornare `service.py` — `send_shutdown_notification`**

```python
# src/runtime_v2/control_plane/service.py — send_shutdown_notification
# Sostituire il payload dict:

# prima
payload={
    "level": "INFO",
    "category": "Runtime",
    "description": f"Runtime shutdown — {reason}",
    "source": "runtime_main",
    "context": {
        "reason": reason,
        "open_chains": open_chains,
        "pending_commands": pending_cmds,
    },
},

# dopo
payload={
    "level": "INFO",
    "reason": reason,
    "open_chains": open_chains,
    "pending_commands": pending_cmds,
    "source": "runtime_main",
},
```

- [ ] **Step 3: Aggiornare `outbox_writer.py` — `notify_listener_edit_skipped`**

```python
# src/runtime_v2/control_plane/outbox_writer.py — notify_listener_edit_skipped
# Sostituire il payload dict:

# prima
payload={
    "level": "WARNING",
    "category": "Listener",
    "title": "edit_of_executed_signal_skipped",
    "description": (
        "Edit di un segnale con trade chain già creata — "
        "non riprocessato."
    ),
    "context": context,
    "action": "verifica il messaggio modificato e intervieni manualmente se serve",
    "source": "telegram_listener",
},

# dopo
payload={
    "level": "WARNING",
    "description": "Edit di un segnale con trade chain già creata — non riprocessato.",
    "chat":    context.get("chat"),
    "msg_id":  context.get("msg_id"),
    "edit_ts": context.get("edit_ts"),
    "action":  "verifica il messaggio modificato e intervieni manualmente se serve",
    "source":  "telegram_listener",
},
```

- [ ] **Step 4: Aggiornare `repositories.py` — `cancel_chain_if_all_entries_failed`**

Trovare il `write_tech_log_event(conn, notification_type="GATEWAY_ENTRY_ALL_FAILED", ...)` e sostituire il payload:

```python
# prima
payload={
    "level": "ERROR",
    "category": "Gateway",
    "title": "entry_all_failed",
    "description": "Tutti i comandi PLACE_ENTRY falliti. Catena cancellata.",
    "context": {
        "chain_id": trade_chain_id,
        "symbol": chain_row[1],
        "side": chain_row[2],
        "reason": reason,
    },
    "action": "intervento manuale richiesto",
    "source": "execution_gateway",
},

# dopo
payload={
    "level": "ERROR",
    "description": "Tutti i comandi PLACE_ENTRY falliti. Catena cancellata.",
    "chain_id": trade_chain_id,
    "symbol":   chain_row[1],
    "side":     chain_row[2],
    "reason":   reason,
    "action":   "intervento manuale richiesto",
    "source":   "execution_gateway",
},
```

- [ ] **Step 5: Aggiornare `repositories.py` — `mark_review_required`**

Trovare il `write_tech_log_event(conn, notification_type="GATEWAY_REVIEW_REQUIRED", ...)` e sostituire il payload:

```python
# prima
payload={
    "level": "WARNING",
    "category": "Gateway",
    "title": "command_blocked",
    "description": "Comando bloccato in REVIEW_REQUIRED.",
    "context": {
        "command_id": command_id,
        "command_type": cmd_row[1] if cmd_row else None,
        "chain_id": cmd_row[0] if cmd_row else None,
        "reason": reason,
    },
    "action": "intervento manuale richiesto",
    "source": "execution_gateway",
},

# dopo
payload={
    "level":        "WARNING",
    "description":  "Comando bloccato in REVIEW_REQUIRED.",
    "command_id":   command_id,
    "command_type": cmd_row[1] if cmd_row else None,
    "chain_id":     cmd_row[0] if cmd_row else None,
    "reason":       reason,
    "action":       "intervento manuale richiesto",
    "source":       "execution_gateway",
},
```

- [ ] **Step 6: Implementare `write_command_failed_tech_log` in `repositories.py`**

Aggiungere il metodo alla classe `GatewayCommandRepository`, dopo `write_cancel_entry_failed_lifecycle`:

```python
def write_command_failed_tech_log(
    self, command_id: int, trade_chain_id: int, command_type: str, *, reason: str
) -> None:
    """Write TECH_LOG for permanent failure of a non-entry command (SL, TP, CANCEL)."""
    from src.runtime_v2.control_plane.outbox_writer import write_tech_log_event
    conn = sqlite3.connect(self._db)
    try:
        with conn:
            write_tech_log_event(
                conn,
                notification_type="GATEWAY_COMMAND_FAILED",
                payload={
                    "level":        "ERROR",
                    "command_id":   command_id,
                    "command_type": command_type,
                    "chain_id":     trade_chain_id,
                    "reason":       reason,
                    "source":       "execution_gateway",
                },
                dedupe_key=f"gw_cmd_failed:{command_id}",
                priority="HIGH",
            )
    finally:
        conn.close()
```

- [ ] **Step 7: Verificare tutti i test**

```
pytest tests/runtime_v2/control_plane/ -v
```
Atteso: tutti i test PASS, inclusi `test_tech_log_policy.py` e `test_tech_log_formatter.py`.

- [ ] **Step 8: Commit**

```bash
git add src/runtime_v2/control_plane/service.py src/runtime_v2/control_plane/outbox_writer.py src/runtime_v2/execution_gateway/repositories.py
git commit -m "feat: flatten tech_log callsite payloads and implement write_command_failed_tech_log"
```

---

## Self-review

**Spec coverage:**
- ✅ `templates/tech_log.py` con 6 template — Task 1
- ✅ helper `_tech_header` senza modifiche a `_blocks.py` — Task 1
- ✅ `tech_log.py` thin dispatcher con `notification_type` — Task 2
- ✅ `notification_dispatcher.py` aggiornato — Task 2
- ✅ `delivery_mode` nel dispatcher thin, non nei template — Task 2
- ✅ Payload flat per tutti i 6 callsite — Task 3
- ✅ `write_command_failed_tech_log` implementato — Task 3
- ✅ `TEMPLATE_REGISTRY` con i 6 tipi — Task 1
- ✅ Fallback per tipi non in registry — Task 2

**Placeholder scan:** nessun TBD, nessun "similar to Task N". Ogni step ha codice esplicito.

**Type consistency:**
- `format_tech_log(notification_type: str, payload: dict, *, delivery_mode: str) -> str` — definita in Task 2, usata dal dispatcher.
- `TEMPLATE_REGISTRY: dict[str, TemplateConfig]` — definita in Task 1, importata in Task 2.
- `write_command_failed_tech_log(self, command_id: int, trade_chain_id: int, command_type: str, *, reason: str) -> None` — definita e usata in Task 3.
