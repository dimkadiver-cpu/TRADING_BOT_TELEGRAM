# MOVE_STOP No-Price Fallback to BE — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Quando `MOVE_STOP` non ha né `new_stop_price` né `stop_to_tp_level`, il translator produce `SetStopOperation(target_type="ENTRY")` invece di `None`, evitando `parse_status=ERROR` e aggiungendo il warning `"move_stop_no_price_defaulted_to_be"`.

**Architecture:** Modifica a `_operation_from_intent` in `canonical_translator.py`: il ramo `else` restituisce `SetStopOperation(target_type="ENTRY")` invece di `None`. In `translate()`, dopo aver costruito `intent_op_pairs`, un loop detecta i MOVE_STOP con `target_type="ENTRY"` e appende il warning. Nessuna modifica alle firme né ad altri file.

**Tech Stack:** Python 3.12, Pydantic v2, pytest

---

## File Map

| File | Azione |
|---|---|
| `src/parser_v2/translation/canonical_translator.py` | Modifica — 2 punti: `_operation_from_intent` e `translate()` |
| `src/parser_v2/tests/test_canonical_translator_v2.py` | Modifica — aggiunge 3 test |

---

## Task 1: Test TDD — scrivi i test che devono fallire

**Files:**
- Modify: `src/parser_v2/tests/test_canonical_translator_v2.py`

- [ ] **Step 1: Aggiungi i 3 test a `test_canonical_translator_v2.py`**

Apri il file. In fondo, dopo l'ultimo test, aggiungi:

```python
def test_move_stop_no_price_defaults_to_be() -> None:
    """MOVE_STOP senza prezzo né tp_level → SET_STOP/ENTRY + warning."""
    from src.parser_v2.contracts.entities import MoveStopEntities

    intent = ParsedIntent(
        type="MOVE_STOP",
        category="UPDATE",
        confidence=0.8,
        entities=MoveStopEntities(),  # new_stop_price=None, stop_to_tp_level=None
        intent_id="MOVE_STOP#0",
        occurrence_index=0,
    )
    parsed = _make_parsed([intent])
    result = CanonicalTranslator().translate(parsed)

    assert result.parse_status == "PARSED"
    ops = result.update.operations
    assert len(ops) == 1
    assert ops[0].op_type == "SET_STOP"
    assert ops[0].set_stop is not None
    assert ops[0].set_stop.target_type == "ENTRY"
    assert "move_stop_no_price_defaulted_to_be" in result.warnings


def test_move_stop_with_price_no_warning() -> None:
    """MOVE_STOP con prezzo → comportamento invariato, nessun nuovo warning."""
    from src.parser_v2.contracts.entities import MoveStopEntities, Price

    intent = ParsedIntent(
        type="MOVE_STOP",
        category="UPDATE",
        confidence=0.9,
        entities=MoveStopEntities(new_stop_price=Price(raw="89000", value=89000.0)),
        intent_id="MOVE_STOP#0",
        occurrence_index=0,
    )
    parsed = _make_parsed([intent])
    result = CanonicalTranslator().translate(parsed)

    assert result.parse_status == "PARSED"
    ops = result.update.operations
    assert len(ops) == 1
    assert ops[0].set_stop.target_type == "PRICE"
    assert ops[0].set_stop.price.value == 89000.0
    assert "move_stop_no_price_defaulted_to_be" not in result.warnings


def test_move_stop_to_be_intent_no_new_warning() -> None:
    """MOVE_STOP_TO_BE (intent distinto) non acquisisce il nuovo warning."""
    intent = _make_intent("MOVE_STOP_TO_BE")
    parsed = _make_parsed([intent])
    result = CanonicalTranslator().translate(parsed)

    assert result.parse_status == "PARSED"
    assert "move_stop_no_price_defaulted_to_be" not in result.warnings
```

- [ ] **Step 2: Verifica che i test falliscano**

```
pytest src/parser_v2/tests/test_canonical_translator_v2.py::test_move_stop_no_price_defaults_to_be src/parser_v2/tests/test_canonical_translator_v2.py::test_move_stop_with_price_no_warning src/parser_v2/tests/test_canonical_translator_v2.py::test_move_stop_to_be_intent_no_new_warning -v
```

Expected: `test_move_stop_no_price_defaults_to_be` FAIL (parse_status=ERROR, ops vuote), gli altri 2 PASS già.

---

## Task 2: Implementazione — `canonical_translator.py`

**Files:**
- Modify: `src/parser_v2/translation/canonical_translator.py`

### Modifica A — `_operation_from_intent`, ramo MOVE_STOP (righe 189-203)

- [ ] **Step 1: Sostituisci il ramo `else: return None` con fallback ENTRY**

Trova il blocco:

```python
    if intent.type == "MOVE_STOP" and isinstance(entities, MoveStopEntities):
        if entities.new_stop_price is not None:
            set_stop = SetStopOperation(target_type="PRICE", price=entities.new_stop_price)
        elif entities.stop_to_tp_level is not None:
            set_stop = SetStopOperation(target_type="TP_LEVEL", tp_level=entities.stop_to_tp_level)
        else:
            return None
        return UpdateOperation(
            op_type="SET_STOP",
            set_stop=set_stop,
            source_intent=intent.type,
            source_intent_id=intent.intent_id,
            confidence=intent.confidence,
            raw_fragment=intent.raw_fragment,
        )
```

Sostituiscilo con:

```python
    if intent.type == "MOVE_STOP" and isinstance(entities, MoveStopEntities):
        if entities.new_stop_price is not None:
            set_stop = SetStopOperation(target_type="PRICE", price=entities.new_stop_price)
        elif entities.stop_to_tp_level is not None:
            set_stop = SetStopOperation(target_type="TP_LEVEL", tp_level=entities.stop_to_tp_level)
        else:
            set_stop = SetStopOperation(target_type="ENTRY")
        return UpdateOperation(
            op_type="SET_STOP",
            set_stop=set_stop,
            source_intent=intent.type,
            source_intent_id=intent.intent_id,
            confidence=intent.confidence,
            raw_fragment=intent.raw_fragment,
        )
```

### Modifica B — `translate()`, aggiunta warning (nel branch `parsed.primary_class == "UPDATE"`)

- [ ] **Step 2: Aggiungi il loop warning dopo la costruzione di `intent_op_pairs`**

Trova le righe (circa 96-97):

```python
            intent_op_pairs = [(i, op) for i, op in intent_op_pairs if op is not None]
```

Dopo questa riga, aggiungi:

```python
            for _intent, _op in intent_op_pairs:
                if (
                    _intent.type == "MOVE_STOP"
                    and _op.set_stop is not None
                    and _op.set_stop.target_type == "ENTRY"
                ):
                    warnings = _append_once(warnings, "move_stop_no_price_defaulted_to_be")
```

- [ ] **Step 3: Verifica che tutti e 3 i test passino**

```
pytest src/parser_v2/tests/test_canonical_translator_v2.py::test_move_stop_no_price_defaults_to_be src/parser_v2/tests/test_canonical_translator_v2.py::test_move_stop_with_price_no_warning src/parser_v2/tests/test_canonical_translator_v2.py::test_move_stop_to_be_intent_no_new_warning -v
```

Expected: tutti e 3 PASS.

- [ ] **Step 4: Verifica suite completa parser_v2**

```
pytest src/parser_v2/tests/ -q
```

Expected: tutti PASS (nessuna regressione).

- [ ] **Step 5: Commit**

```
git add src/parser_v2/translation/canonical_translator.py src/parser_v2/tests/test_canonical_translator_v2.py
git commit -m "fix(canonical_translator): MOVE_STOP without price falls back to SET_STOP/ENTRY + warning"
```
