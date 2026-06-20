# Tech Log Templating System — Block-based DSL

**Data**: 2026-06-18
**Stato**: Approvato

---

## Obiettivo

Riprogettare `tech_log.py` con la stessa architettura a blocchi del clean_log: template dichiarativi per notification type, facilmente modificabili senza toccare la logica di rendering. Il formatter diventa type-aware (riceve `notification_type` dal dispatcher) e i payload dei callsite vengono flattened.

---

## Contesto attuale

- `src/runtime_v2/control_plane/formatters/tech_log.py` — ~35 righe, un solo formatter generico per tutti i tipi. Non riceve `notification_type`. Il payload usa `context` dict annidato.
- 5 notification type esistenti: `RUNTIME_STARTUP`, `RUNTIME_SHUTDOWN`, `LISTENER_EDIT_SKIPPED`, `GATEWAY_ENTRY_ALL_FAILED`, `GATEWAY_REVIEW_REQUIRED`.
- `write_command_failed_tech_log` è chiamato in `gateway.py` (righe 335 e 391) ma **non esiste** in `repositories.py` — fallimenti permanenti su comandi SL/TP/CANCEL spariscono in silenzio.
- Il `delivery_mode` (`supergroup_topics` vs `private_bot`) è gestito internamente nel formatter.

---

## Struttura file dopo la migrazione

```
src/runtime_v2/control_plane/formatters/
├── _blocks.py              ← invariato (condiviso con clean_log)
├── _formatters.py          ← invariato (condiviso con clean_log)
├── templates/
│   ├── __init__.py         ← invariato
│   ├── clean_log.py        ← invariato
│   └── tech_log.py         ← NUOVO: helper header + template per tipo + REGISTRY
├── clean_log.py            ← invariato
├── tech_log.py             ← thin dispatcher (~15 righe)
└── display.py              ← invariato
```

`_blocks.py` e `_formatters.py` restano invariati — nessun nuovo block type aggiunto all'engine.

---

## Infrastruttura condivisa

Il tech_log riusa integralmente `_blocks.py` (block dataclass + `render_template`) e `_formatters.py` (formatter `num`, `text`, `money`, ecc.) del clean_log. Zero duplicazione dell'engine.

---

## Helper header (`templates/tech_log.py`)

L'header di ogni template tech_log è definito tramite una funzione helper locale che restituisce due blocchi primitivi esistenti. Nessuna modifica a `_blocks.py`.

```python
def _tech_header(emoji: str, category: str, event_label: str) -> list[Block]:
    return [
        DerivedBlock(text_fn=lambda p, _e=emoji, _c=category, _l=event_label:
            f"{_e} {_c}: {_l}"),
        SeparatorBlock(),
    ]
```

Output prodotto:
```
⚠️ GATEWAY: REVIEW REQUIRED
────────────────
```

---

## Dispatcher thin (`tech_log.py`)

```python
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

Il `delivery_mode` viene gestito nel dispatcher thin — non nei template. I template producono solo il body.

---

## Modifica `notification_dispatcher.py`

Una riga: `notification_type` viene passato al formatter (attualmente non lo fa).

```python
# prima
return format_tech_log(payload, delivery_mode=self._config.delivery_mode)

# dopo
return format_tech_log(notification_type, payload, delivery_mode=self._config.delivery_mode)
```

---

## Payload schema — flat

I payload di tutti i callsite vengono riscritti in forma flat. Il dict annidato `context` viene eliminato. I campi `level`, `category`, `title` vengono rimossi — sono hardcoded nei template.

---

## Notification types e template

### 1. RUNTIME_STARTUP

```
ℹ️ RUNTIME: AVVIATO
────────────────
Started at: 2026-06-18 10:00:00 UTC
────────────────
Source: runtime_main
```

**Payload flat**: `started_at`, `source`

```python
TemplateConfig([
    *_tech_header("ℹ️", "RUNTIME", "AVVIATO"),
    FieldBlock("Started at", key="started_at", fmt=text, optional=False, default="n/a"),
    FooterBlock(default_source="runtime_main"),
])
```

**Callsite aggiornato** (`service.py → send_startup_notification`):
```python
# prima
"context": {"started_at": "..."}
# dopo
"started_at": "...",
"source": "runtime_main",
```

---

### 2. RUNTIME_SHUTDOWN

```
ℹ️ RUNTIME: SHUTDOWN
────────────────
Reason: SIGTERM
Open chains: 3
Pending commands: 1
────────────────
Source: runtime_main
```

**Payload flat**: `reason`, `open_chains`, `pending_commands`, `source`

```python
TemplateConfig([
    *_tech_header("ℹ️", "RUNTIME", "SHUTDOWN"),
    FieldBlock("Reason",           key="reason",           fmt=text, optional=False, default="n/a"),
    FieldBlock("Open chains",      key="open_chains",      fmt=num,  optional=False, default="n/a"),
    FieldBlock("Pending commands", key="pending_commands", fmt=num,  optional=False, default="n/a"),
    FooterBlock(default_source="runtime_main"),
])
```

**Callsite aggiornato** (`service.py → send_shutdown_notification`):
```python
# prima
"context": {"reason": reason, "open_chains": ..., "pending_commands": ...}
# dopo
"reason": reason,
"open_chains": open_chains,
"pending_commands": pending_cmds,
"source": "runtime_main",
```

---

### 3. LISTENER_EDIT_SKIPPED

```
⚠️ LISTENER: EDIT SKIPPED
────────────────
Edit di un segnale con trade chain già creata — non riprocessato.
Chat: -100123456
Msg ID: 789
Action: verifica il messaggio e intervieni manualmente
────────────────
Source: telegram_listener
```

**Payload flat**: `description`, `chat`, `msg_id`, `edit_ts`, `action`, `source`

```python
TemplateConfig([
    *_tech_header("⚠️", "LISTENER", "EDIT SKIPPED"),
    DerivedBlock(text_fn=lambda p: p.get("description") or ""),
    FieldBlock("Chat",    key="chat",    fmt=text),
    FieldBlock("Msg ID",  key="msg_id",  fmt=text),
    FieldBlock("Edit ts", key="edit_ts", fmt=text, optional=True),
    FieldBlock("Action",  key="action",  fmt=text, optional=True),
    FooterBlock(default_source="telegram_listener"),
])
```

**Callsite aggiornato** (`outbox_writer.py → notify_listener_edit_skipped`):
```python
# prima
"context": {"chat": ..., "msg_id": ..., "edit_ts": ...}
# dopo
"chat":    context.get("chat"),
"msg_id":  context.get("msg_id"),
"edit_ts": context.get("edit_ts"),
"description": "Edit di un segnale con trade chain già creata — non riprocessato.",
"action":  "verifica il messaggio modificato e intervieni manualmente se serve",
"source":  "telegram_listener",
```

---

### 4. GATEWAY_ENTRY_ALL_FAILED

```
🛑 GATEWAY: ENTRY ALL FAILED
────────────────
Tutti i comandi PLACE_ENTRY falliti. Catena cancellata.
Chain: #42
Symbol: BTC/USDT
Side: LONG
Reason: order rejected by exchange
Action: intervento manuale richiesto
────────────────
Source: execution_gateway
```

**Payload flat**: `description`, `chain_id`, `symbol`, `side`, `reason`, `action`, `source`, `link` (opzionale)

```python
TemplateConfig([
    *_tech_header("🛑", "GATEWAY", "ENTRY ALL FAILED"),
    DerivedBlock(text_fn=lambda p: p.get("description") or ""),
    FieldBlock("Chain",  value_fn=lambda p: f"#{p['chain_id']}" if p.get("chain_id") is not None else None,
               fmt=text, optional=True),
    FieldBlock("Symbol", key="symbol", fmt=display_symbol, optional=True),
    FieldBlock("Side",   key="side",   fmt=text, optional=True),
    FieldBlock("Reason", key="reason", fmt=text, optional=False, default="n/a"),
    FieldBlock("Action", key="action", fmt=text, optional=True),
    FooterBlock(default_source="execution_gateway"),
])
```

**Callsite aggiornato** (`repositories.py → cancel_chain_if_all_entries_failed`):
```python
# prima
"context": {"chain_id": ..., "symbol": ..., "side": ..., "reason": ...}
# dopo
"chain_id":   trade_chain_id,
"symbol":     chain_row[1],
"side":       chain_row[2],
"reason":     reason,
"description": "Tutti i comandi PLACE_ENTRY falliti. Catena cancellata.",
"action":     "intervento manuale richiesto",
"source":     "execution_gateway",
```

---

### 5. GATEWAY_REVIEW_REQUIRED

```
⚠️ GATEWAY: REVIEW REQUIRED
────────────────
Comando bloccato in REVIEW_REQUIRED.
Command: PLACE_ENTRY
Chain: #42
Reason: capability_missing:can_place_limit_entry
Action: intervento manuale richiesto
────────────────
Source: execution_gateway
```

**Payload flat**: `description`, `command_id`, `command_type`, `chain_id`, `reason`, `action`, `source`, `link` (opzionale)

```python
TemplateConfig([
    *_tech_header("⚠️", "GATEWAY", "REVIEW REQUIRED"),
    DerivedBlock(text_fn=lambda p: p.get("description") or ""),
    FieldBlock("Command", key="command_type", fmt=text, optional=True),
    FieldBlock("Chain",   value_fn=lambda p: f"#{p['chain_id']}" if p.get("chain_id") is not None else None,
               fmt=text, optional=True),
    FieldBlock("Reason",  key="reason",       fmt=text, optional=False, default="n/a"),
    FieldBlock("Action",  key="action",        fmt=text, optional=True),
    FooterBlock(default_source="execution_gateway"),
])
```

**Callsite aggiornato** (`repositories.py → mark_review_required`):
```python
# prima
"context": {"command_id": ..., "command_type": ..., "chain_id": ..., "reason": ...}
# dopo
"command_id":   command_id,
"command_type": cmd_row[1] if cmd_row else None,
"chain_id":     cmd_row[0] if cmd_row else None,
"reason":       reason,
"description":  "Comando bloccato in REVIEW_REQUIRED.",
"action":       "intervento manuale richiesto",
"source":       "execution_gateway",
```

---

### 6. GATEWAY_COMMAND_FAILED *(nuovo)*

Copre i fallimenti permanenti su comandi non-entry (SET_SL, SET_TP, CANCEL_PENDING_ENTRY, ecc.) che oggi spariscono in silenzio. Implementa il metodo mancante `write_command_failed_tech_log` in `GatewayCommandRepository`.

```
🛑 GATEWAY: COMMAND FAILED
────────────────
Command: SET_SL
Chain: #42
Reason: KeyError: 'order_id'
────────────────
Source: execution_gateway
```

**Payload flat**: `command_id`, `command_type`, `chain_id`, `reason`, `source`

```python
TemplateConfig([
    *_tech_header("🛑", "GATEWAY", "COMMAND FAILED"),
    FieldBlock("Command", key="command_type", fmt=text, optional=True),
    FieldBlock("Chain",   value_fn=lambda p: f"#{p['chain_id']}" if p.get("chain_id") is not None else None,
               fmt=text, optional=True),
    FieldBlock("Reason",  key="reason",       fmt=text, optional=False, default="n/a"),
    FooterBlock(default_source="execution_gateway"),
])
```

**Metodo da implementare** (`repositories.py`):
```python
def write_command_failed_tech_log(
    self, command_id: int, trade_chain_id: int, command_type: str, *, reason: str
) -> None:
    from src.runtime_v2.control_plane.outbox_writer import write_tech_log_event
    conn = sqlite3.connect(self._db)
    try:
        with conn:
            write_tech_log_event(
                conn,
                notification_type="GATEWAY_COMMAND_FAILED",
                payload={
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

---

## TEMPLATE_REGISTRY

```python
TEMPLATE_REGISTRY: dict[str, TemplateConfig] = {
    "RUNTIME_STARTUP":          TemplateConfig([...]),
    "RUNTIME_SHUTDOWN":         TemplateConfig([...]),
    "LISTENER_EDIT_SKIPPED":    TemplateConfig([...]),
    "GATEWAY_ENTRY_ALL_FAILED": TemplateConfig([...]),
    "GATEWAY_REVIEW_REQUIRED":  TemplateConfig([...]),
    "GATEWAY_COMMAND_FAILED":   TemplateConfig([...]),
}
```

Aggiungere un nuovo tipo significa aggiungere una voce al registry — zero modifiche all'engine.

---

## Invarianti garantite

| Cosa | Prima | Dopo |
|------|-------|------|
| Modificare un campo in RUNTIME_SHUTDOWN | Toccare `format_tech_log` in `tech_log.py` | Toccare il template in `templates/tech_log.py` |
| Aggiungere un nuovo notification type | Aggiungere logica al formatter generico | Definire template + entry nel registry |
| Separatori dinamici | `_finalize` (clean_log) | `_finalize` invariato (condiviso) |
| delivery_mode | Gestito nel formatter | Gestito nel dispatcher thin |
| Payload schema | `context` dict annidato | Flat — tutti i campi top-level |

---

## Fuori scope (rimandato)

- `PARSER_ERROR` — richiede callback injection in TelegramListener; architettura nota, rimandato.
- `FILL_IGNORED` — fill persi per coid non parsabile; rimandato.
- `LIFECYCLE_EVENT_FAILED` — exception in `workers.py` su exchange_event; rimandato.

---

## Dipendenze nuove

Nessuna. Zero pacchetti aggiuntivi.

---

## Strategia di migrazione

Migrazione completa — il nuovo sistema sostituisce interamente il formatter generico.

1. Creare `templates/tech_log.py` con helper header + 6 template + TEMPLATE_REGISTRY.
2. Riscrivere `tech_log.py` come thin dispatcher (riceve `notification_type`).
3. Aggiornare `notification_dispatcher.py` (una riga — passa `notification_type`).
4. Aggiornare i 5 callsite esistenti (payload flat).
5. Implementare `write_command_failed_tech_log` in `repositories.py`.
