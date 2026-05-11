# MOVE_STOP No-Price Fallback Design

## Goal

Quando un intent `MOVE_STOP` non ha né `new_stop_price` né `stop_to_tp_level`, il canonical translator restituisce `None` causando `parse_status=ERROR`. Il fix introduce un fallback a `target_type="ENTRY"` (breakeven) con warning esplicito, mantenendo il messaggio parsabile.

## Scope

Un solo file: `src/parser_v2/translation/canonical_translator.py`. Nessuna modifica a profili, contratti o modelli. Applicato a tutti i profili (trader_a, trader_b, trader_c) in quanto centrale.

## Caso reale che motiva il fix

Message raw_message_id=30, trader_b: `"Стоп лосс переносим за минимум как показано на графике"` — MOVE_STOP senza livello numerico (il target è visivo, sul grafico). Attuale output: `parse_status=ERROR`, warning `canonical_translation_without_update_operation`. Output atteso dopo fix: `parse_status=PARSED`, operazione `SET_STOP/ENTRY`, warning `move_stop_no_price_defaulted_to_be`.

## Architettura

### Modifica 1 — `_operation_from_intent`, ramo MOVE_STOP

**Prima:**
```python
if intent.type == "MOVE_STOP" and isinstance(entities, MoveStopEntities):
    if entities.new_stop_price is not None:
        set_stop = SetStopOperation(target_type="PRICE", price=entities.new_stop_price)
    elif entities.stop_to_tp_level is not None:
        set_stop = SetStopOperation(target_type="TP_LEVEL", tp_level=entities.stop_to_tp_level)
    else:
        return None
```

**Dopo:**
```python
if intent.type == "MOVE_STOP" and isinstance(entities, MoveStopEntities):
    if entities.new_stop_price is not None:
        set_stop = SetStopOperation(target_type="PRICE", price=entities.new_stop_price)
    elif entities.stop_to_tp_level is not None:
        set_stop = SetStopOperation(target_type="TP_LEVEL", tp_level=entities.stop_to_tp_level)
    else:
        set_stop = SetStopOperation(target_type="ENTRY")  # fallback: no price → BE
```

Nessuna modifica alla firma. Il `return UpdateOperation(...)` esistente gestisce tutti e tre i casi.

### Modifica 2 — `translate()`, warning detection

Dopo la costruzione di `intent_op_pairs`, aggiungere il warning quando un MOVE_STOP ha prodotto `target_type="ENTRY"` (indicatore del fallback):

```python
for intent, op in intent_op_pairs:
    if (
        intent.type == "MOVE_STOP"
        and op.set_stop is not None
        and op.set_stop.target_type == "ENTRY"
    ):
        warnings = _append_once(warnings, "move_stop_no_price_defaulted_to_be")
```

Il warning distingue il fallback da un genuino `MOVE_STOP_TO_BE` (che arriva tramite intent type diverso).

## Contratto output

| Campo | Valore |
|---|---|
| `parse_status` | `PARSED` |
| `op_type` | `SET_STOP` |
| `set_stop.target_type` | `ENTRY` |
| `warnings` | `["move_stop_no_price_defaulted_to_be"]` |

## Test

- MOVE_STOP con prezzo: comportamento invariato (`PRICE`, nessun warning)
- MOVE_STOP con tp_level: comportamento invariato (`TP_LEVEL`, nessun warning)
- MOVE_STOP senza prezzo né tp_level: `ENTRY` + warning `move_stop_no_price_defaulted_to_be`
- Regressione: MOVE_STOP_TO_BE non acquisisce il nuovo warning

## File toccati

| File | Azione |
|---|---|
| `src/parser_v2/translation/canonical_translator.py` | Modifica — 2 punti |
| `src/parser_v2/tests/test_canonical_translator_v2.py` | Modifica — aggiungi test |
