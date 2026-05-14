# Design: TargetActionGroup — Modello canonico target-centric per UPDATE

**Data:** 2026-05-14  
**Scope:** `src/parser_v2`  
**Status:** Draft — in attesa di approvazione

---

## Problema

Il modello attuale di `parser_v2` per messaggi UPDATE ha due path distinte:

- `update: UpdatePayload` → operazioni senza target (single-target message-wide)
- `targeted_actions: list[TargetedAction]` → operazioni con target espliciti (action-centric)

Questo crea tre problemi concreti:

1. **Caso misto non supportato**: messaggi con target diversi e azioni diverse per target producono `PARTIAL` anche quando il testo è chiaramente parsabile (es. `link 2712 стоп на 1 тейк / link 2713 стоп в бу`).

2. **Executor deve ricostruire il target**: con `targeted_actions` action-centric, l'executor deve incrociare azioni e target per sapere "cosa fare su questo ref specifico". Se un'azione è eseguibile e l'altra no, il codice è fragile.

3. **`params: dict[str, Any]`** non è tipizzato — nessuna validazione Pydantic sui parametri dell'azione.

---

## Casi reali (Trader A)

Cinque pattern osservati nei dati:

```
Caso 1 — 3 link + 1 azione
  https://t.me/c/3171748254/2712
  https://t.me/c/3171748254/2713
  https://t.me/c/3171748254/2718
  Отменяем лимитки

Caso 2 — scope globale + 1 azione
  Закрываю все позиции по текущим

Caso 3 — 2 link, azione diversa per target
  https://t.me/c/3171748254/2712 стоп на 1 тейк
  https://t.me/c/3171748254/2713 стоп в бу

Caso 4 — 3 link + 2 azioni sugli stessi target
  https://t.me/c/3171748254/2712
  https://t.me/c/3171748254/2713
  https://t.me/c/3171748254/2718
  стоп в бу отменяем лимитки

Caso 5 — scope globale + 2 azioni
  все позиции стоп в бу убираем лимитку
```

---

## Decisione

Sostituire `update: UpdatePayload` + `targeted_actions: list[TargetedAction]` con un'unica struttura target-centric:

```
target_action_groups: list[TargetActionGroup]
```

Ogni `TargetActionGroup` raggruppa:
- **chi** è il target (refs o scope)
- **cosa fare** su quel target (lista di azioni tipizzate)

Tutti e 5 i casi usano la stessa struttura. Nessuna doppia path.

---

## Modello

### `ActionItem`

Sostituisce `TargetedAction`. Params tipizzati via campi dedicati (stesso pattern di `UpdateOperation`).

```python
class ActionItem(CanonicalModel):
    action_type: UpdateOperationType
    set_stop: SetStopOperation | None = None
    close: CloseOperation | None = None
    cancel_pending: CancelPendingOperation | None = None
    modify_entries: ModifyEntriesOperation | None = None
    modify_targets: ModifyTargetsOperation | None = None
    invalidate_setup: InvalidateSetupOperation | None = None
    source_intent: IntentType
    source_intent_id: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    raw_fragment: str | None = None

    @model_validator(mode="after")
    def _validate_payload(self) -> ActionItem:
        # stesso pattern di UpdateOperation: solo il campo corrispondente ad action_type
        # deve essere popolato
        ...
```

### `TargetActionGroup`

```python
class TargetActionGroup(CanonicalModel):
    targeting: TargetHints                   # target primari (link, id, scope)
    secondary_targeting: TargetHints | None = None  # reply degradato
    actions: list[ActionItem]                # azioni da eseguire su questi target
```

**`secondary_targeting`**: quando il messaggio ha link/id espliciti **ed è anche una reply**, il `reply_to_message_id` viene spostato in `secondary_targeting` invece di `targeting`. Il reply è preservato ma non guida il routing delle azioni.

Regola di assegnazione:
- Messaggio con link/id espliciti → `targeting.telegram_message_ids` (o `telegram_links`); reply → `secondary_targeting`
- Messaggio reply-only → `targeting.reply_to_message_id`; `secondary_targeting = None`
- Messaggio scope globale → `targeting.scope_hint`; `secondary_targeting = None`

### `CanonicalMessage` — campi modificati

```python
# RIMOSSI
update: UpdatePayload | None = None
targeted_actions: list[TargetedAction] = []

# AGGIUNTO
target_action_groups: list[TargetActionGroup] = []
```

Il validator `_validate_primary_class_payloads` aggiorna la condizione `has_update_work`:

```python
has_update_work = bool(self.target_action_groups)
```

---

## I 5 casi in JSON canonico

### Caso 1 — 3 link, 1 azione

```json
{
  "primary_class": "UPDATE",
  "parse_status": "PARSED",
  "target_action_groups": [
    {
      "targeting": { "telegram_message_ids": [2712, 2713, 2718], "scope_hint": "SINGLE_SIGNAL" },
      "actions": [
        { "action_type": "CANCEL_PENDING", "cancel_pending": { "cancel_scope_hint": "TARGETED" }, "source_intent": "CANCEL_PENDING_ORDERS" }
      ]
    }
  ]
}
```

### Caso 2 — scope globale, 1 azione

```json
{
  "primary_class": "UPDATE",
  "parse_status": "PARSED",
  "target_action_groups": [
    {
      "targeting": { "scope_hint": "ALL_POSITIONS" },
      "actions": [
        { "action_type": "CLOSE", "close": { "close_scope": "FULL" }, "source_intent": "CLOSE_FULL" }
      ]
    }
  ]
}
```

### Caso 3 — 2 link, azione diversa per target (era PARTIAL, ora PARSED)

```json
{
  "primary_class": "UPDATE",
  "parse_status": "PARSED",
  "target_action_groups": [
    {
      "targeting": { "telegram_message_ids": [2712], "scope_hint": "SINGLE_SIGNAL" },
      "actions": [
        { "action_type": "SET_STOP", "set_stop": { "target_type": "TP_LEVEL", "tp_level": 1 }, "source_intent": "MOVE_STOP" }
      ]
    },
    {
      "targeting": { "telegram_message_ids": [2713], "scope_hint": "SINGLE_SIGNAL" },
      "actions": [
        { "action_type": "SET_STOP", "set_stop": { "target_type": "ENTRY" }, "source_intent": "MOVE_STOP_TO_BE" }
      ]
    }
  ]
}
```

### Caso 4 — 3 link, 2 azioni sugli stessi target

```json
{
  "primary_class": "UPDATE",
  "parse_status": "PARSED",
  "target_action_groups": [
    {
      "targeting": { "telegram_message_ids": [2712, 2713, 2718], "scope_hint": "SINGLE_SIGNAL" },
      "actions": [
        { "action_type": "SET_STOP", "set_stop": { "target_type": "ENTRY" }, "source_intent": "MOVE_STOP_TO_BE" },
        { "action_type": "CANCEL_PENDING", "cancel_pending": { "cancel_scope_hint": "TARGETED" }, "source_intent": "CANCEL_PENDING_ORDERS" }
      ]
    }
  ]
}
```

### Caso 5 — scope globale, 2 azioni

```json
{
  "primary_class": "UPDATE",
  "parse_status": "PARSED",
  "target_action_groups": [
    {
      "targeting": { "scope_hint": "ALL_POSITIONS" },
      "actions": [
        { "action_type": "SET_STOP", "set_stop": { "target_type": "ENTRY" }, "source_intent": "MOVE_STOP_TO_BE" },
        { "action_type": "CANCEL_PENDING", "cancel_pending": { "cancel_scope_hint": "ALL_PENDING_ENTRIES" }, "source_intent": "CANCEL_PENDING_ORDERS" }
      ]
    }
  ]
}
```

### Caso singolo ref — reply

```json
{
  "primary_class": "UPDATE",
  "parse_status": "PARSED",
  "target_action_groups": [
    {
      "targeting": { "reply_to_message_id": 12345, "scope_hint": "SINGLE_SIGNAL" },
      "actions": [
        { "action_type": "SET_STOP", "set_stop": { "target_type": "ENTRY" }, "source_intent": "MOVE_STOP_TO_BE" }
      ]
    }
  ]
}
```

### Caso singolo ref — link esplicito + reply degradato

```json
{
  "primary_class": "UPDATE",
  "parse_status": "PARSED",
  "target_action_groups": [
    {
      "targeting": { "telegram_message_ids": [2712], "scope_hint": "SINGLE_SIGNAL" },
      "secondary_targeting": { "reply_to_message_id": 12345 },
      "actions": [
        { "action_type": "SET_STOP", "set_stop": { "target_type": "ENTRY" }, "source_intent": "MOVE_STOP_TO_BE" }
      ]
    }
  ]
}
```

---

## Regole del parser (canonical_translator)

```
1. Estrai tutti i target dal messaggio (links, ids, reply, scope)

2. Se presenti link/id espliciti:
   → targeting.telegram_message_ids / telegram_links
   → se il messaggio è anche reply: secondary_targeting.reply_to_message_id

3. Se nessun link/id ma presente reply:
   → targeting.reply_to_message_id

4. Se nessun ref ma scope globale:
   → targeting.scope_hint

5. Per ogni gruppo di target con la stessa firma semantica (action_type + params):
   → 1 TargetActionGroup con targeting comune

6. Per target con azioni diverse (caso 3):
   → 1 TargetActionGroup per target, ognuno con le proprie actions

7. Se impossibile determinare l'associazione ref→azione:
   → parse_status=PARTIAL + warning "ambiguous_target_intent_binding"
```

---

## Impatto downstream — executor loop

Con `target_action_groups` target-centric, l'executor itera in modo uniforme:

```python
for group in msg.target_action_groups:
    positions = resolver.resolve(group.targeting)
    for position in positions:
        for action in group.actions:
            executor.try_execute(action, position)
            # skip se non eseguibile, senza impatto sulle altre azioni
```

Nessuna cross-join azioni/target. Un'azione non eseguibile su un target non blocca le altre.

---

## File impattati

| File | Tipo di modifica |
|---|---|
| `src/parser_v2/contracts/canonical_message.py` | Schema: rimuovi `update`/`targeted_actions`, aggiungi `target_action_groups`, `ActionItem`, `TargetActionGroup`; aggiorna validator |
| `src/parser_v2/translation/canonical_translator.py` | Logica: costruisce `target_action_groups` invece di `UpdatePayload` + `TargetedAction` |
| `src/parser_v2/tests/test_canonical_translator_phase11.py` | Aggiorna assertions su `.update`, `.targeted_actions` |
| `src/parser_v2/tests/test_runtime_golden_phase13.py` | Aggiorna imports e assertions |
| `src/parser_v2/tests/test_runtime_profile_phase12.py` | Aggiorna assertions su `.update.operations` |
| `src/parser_v2/tests/test_contracts_canonical.py` | Aggiorna costruzione e validazione |
| `src/parser_v2/tests/test_runtime_target_binding.py` | Aggiorna accesso a `.update.operations` e `.targeted_actions` |
| `src/parser_v2/tests/test_integration_design.py` | Aggiorna assertions miste |
| `src/parser_v2/tests/test_canonical_translator_v2.py` | Aggiorna assertions su `targeted_actions` |
| `parser_test/reporting/flatteners_v2.py` | Legge `target_action_groups` invece di `update` + `targeted_actions` |
| `parser_test/reporting/report_schema_v2.py` | Rinomina colonne CSV: `targeted_actions_count` → `target_action_groups_count`, ecc. |
| `parser_test/reporting/tests/test_flatteners_v2.py` | Aggiorna JSON di test |

**Non impattati:** `runtime_v2` (serializza JSON intero), `src/parser/canonical_v1/` (sistema separato).

---

## Strutture rimosse

- `UpdatePayload` — classe rimossa
- `TargetedAction` — classe rimossa
- `CanonicalMessage.update` — campo rimosso
- `CanonicalMessage.targeted_actions` — campo rimosso
- `CanonicalMessage.target_hints` — campo rimosso (le hints sono ora dentro ogni `TargetActionGroup.targeting`)

---

## Non in scope

- Migrazione dei profili `src/parser/trader_profiles/` (usano `canonical_v1`, non `parser_v2`)
- Layer `operation_rules`, `target_resolver` (non ancora implementati per `parser_v2`)
- Logica di risoluzione dei target (rimane downstream, fuori parser)
