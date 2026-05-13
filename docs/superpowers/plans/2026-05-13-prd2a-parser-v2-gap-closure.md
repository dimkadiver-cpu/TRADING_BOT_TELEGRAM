# PRD 2.a — Parser V2 Gap Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chiudere i gap residui di `parser_v2` (RANGE entry structure, test no-mix GAP A7) e verificare zero errori di schema su dati reali trader_a.

**Architecture:** TDD puro — test scritti prima dell'implementazione. Due modifiche chirurgiche ai `signal_extractor.py` (trader_a e trader_b), aggiunta test al translator. Round-trip finale con `replay_parser_v2.py` su `db/live.db`.

**Tech Stack:** Python 3.11, pytest, `src/parser_v2/`, `parser_test/scripts/replay_parser_v2.py`

---

## File map

| File | Azione |
|---|---|
| `src/parser_v2/profiles/trader_a/signal_extractor.py` | Modifica: aggiungere `_ENTRY_RANGE_RE`, `_try_range_entry()`, patch `extract()` |
| `src/parser_v2/profiles/trader_b/signal_extractor.py` | Modifica: stessa patch di trader_a |
| `src/parser_v2/tests/test_signal_extractor_patterns.py` | Modifica: aggiungere test RANGE |
| `src/parser_v2/tests/test_canonical_translator_v2.py` | Modifica: aggiungere test GAP A7 no-mix |
| `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/AUDIT.md` | Modifica: chiudere gap, documentare backlog |

---

## Task 1: Test RANGE per trader_a SignalExtractor

**Files:**
- Modify: `src/parser_v2/tests/test_signal_extractor_patterns.py`

- [ ] **Step 1: Aggiungere i test RANGE (falliscono prima dell'implementazione)**

Aprire `src/parser_v2/tests/test_signal_extractor_patterns.py` e appendere in fondo:

```python
def test_range_entry_format_produces_range_structure() -> None:
    """entry: N-M deve produrre entry_structure=RANGE con 2 leg LIMIT."""
    text = (
        "BTCUSDT Лонг\n"
        "Вход: 64000-66000\n"
        "SL: 62000\n"
        "TP1: 70000\n"
    )
    signal = _extract(text)
    assert signal is not None
    assert signal.entry_structure == "RANGE"
    assert len(signal.entries) == 2
    assert signal.entries[0].entry_type == "LIMIT"
    assert signal.entries[1].entry_type == "LIMIT"
    assert signal.entries[0].price.value == 64000.0
    assert signal.entries[1].price.value == 66000.0
    assert signal.entries[0].sequence == 1
    assert signal.entries[1].sequence == 2


def test_two_discrete_entries_produce_two_step_not_range() -> None:
    """Due entry separate devono produrre TWO_STEP, non RANGE."""
    text = (
        "BTCUSDT Лонг\n"
        "Вход A: 64000\n"
        "Вход B: 66000\n"
        "SL: 62000\n"
        "TP1: 70000\n"
    )
    signal = _extract(text)
    assert signal is not None
    assert signal.entry_structure == "TWO_STEP"
    assert len(signal.entries) == 2


def test_range_entry_english_format() -> None:
    """entry: N-M in formato inglese deve produrre RANGE."""
    text = "ETHUSDT Long\nentry: 2000-2100\nSL: 1900\nTP1: 2300\n"
    signal = _extract(text)
    assert signal is not None
    assert signal.entry_structure == "RANGE"
    assert signal.entries[0].price.value == 2000.0
    assert signal.entries[1].price.value == 2100.0
```

- [ ] **Step 2: Verificare che i test falliscano**

```
pytest src/parser_v2/tests/test_signal_extractor_patterns.py::test_range_entry_format_produces_range_structure src/parser_v2/tests/test_signal_extractor_patterns.py::test_two_discrete_entries_produce_two_step_not_range src/parser_v2/tests/test_signal_extractor_patterns.py::test_range_entry_english_format -v
```

Output atteso: i due test RANGE falliscono (`assert signal.entry_structure == "RANGE"` → ricevono `"TWO_STEP"`). Il test `two_discrete_entries` potrebbe passare già.

---

## Task 2: Implementare RANGE in trader_a SignalExtractor

**Files:**
- Modify: `src/parser_v2/profiles/trader_a/signal_extractor.py`

- [ ] **Step 1: Aggiungere regex `_ENTRY_RANGE_RE` e funzione `_try_range_entry`**

Aprire `src/parser_v2/profiles/trader_a/signal_extractor.py`.

Dopo la riga `_BULLET_CHARS = r"\-*•—–"` (circa riga 28), aggiungere:

```python
_ENTRY_RANGE_RE = re.compile(
    rf"\b(?:entry|vhod|{_CYR_ENTRY})\b"
    rf"(?:\s+(?:\([^)\n]*\)|[^\n:(){{}}]{{1,32}}))?"
    rf"\s*[:=@]?\s*"
    rf"(?P<min>{_NUMBER_PATTERN})\s*[-–—]\s*(?P<max>{_NUMBER_PATTERN})(?!\s*%)",
    re.IGNORECASE,
)
```

Alla fine del file (dopo `_float_from_raw`), aggiungere:

```python
def _try_range_entry(text: str) -> list[EntryLeg] | None:
    match = _ENTRY_RANGE_RE.search(text)
    if not match:
        return None
    min_price = _price_from_raw(match.group("min"))
    max_price = _price_from_raw(match.group("max"))
    if min_price is None or max_price is None:
        return None
    return [
        EntryLeg(sequence=1, entry_type="LIMIT", price=min_price, role="PRIMARY", is_optional=False),
        EntryLeg(sequence=2, entry_type="LIMIT", price=max_price, role="AVERAGING", is_optional=False),
    ]
```

- [ ] **Step 2: Modificare `SignalExtractor.extract()` per usare `_try_range_entry`**

Sostituire il metodo `extract()` (righe 99-131 circa) con:

```python
def extract(self, normalized: NormalizedText, market_hint: bool = False) -> SignalDraft | None:
    text = normalized.raw_text
    normalized_text = normalized.normalized_text

    symbol = normalize_symbol(_extract_symbol(text))
    side = _extract_side(normalized_text)

    range_entries = _try_range_entry(text)
    if range_entries is not None:
        entries = range_entries
        entry_structure = "RANGE"
    else:
        entries = _extract_entries(text, market_hint=market_hint)
        entry_structure = _entry_structure(entries)

    stop_loss = _extract_stop_loss(text)
    take_profits = _extract_take_profits(text)
    risk_hint = _extract_risk_hint(text, self._risk_prefixes, self._risk_suffixes)

    if not any((entries, stop_loss, take_profits)):
        return None

    missing_fields = _missing_fields(
        symbol=symbol,
        side=side,
        entries=entries,
        stop_loss=stop_loss,
        take_profits=take_profits,
    )

    return SignalDraft(
        symbol=symbol,
        side=side,
        entry_structure=entry_structure,
        entries=entries,
        stop_loss=stop_loss,
        take_profits=take_profits,
        risk_hint=risk_hint,
        missing_fields=missing_fields,
        completeness="COMPLETE" if not missing_fields else "INCOMPLETE",
    )
```

- [ ] **Step 3: Eseguire i nuovi test RANGE**

```
pytest src/parser_v2/tests/test_signal_extractor_patterns.py::test_range_entry_format_produces_range_structure src/parser_v2/tests/test_signal_extractor_patterns.py::test_two_discrete_entries_produce_two_step_not_range src/parser_v2/tests/test_signal_extractor_patterns.py::test_range_entry_english_format -v
```

Output atteso: tutti e 3 passano.

- [ ] **Step 4: Eseguire la suite completa di parser_v2 per verificare no regressioni**

```
pytest src/parser_v2/tests/ -v
```

Output atteso: tutti i test passano (≥ 94 + 3 nuovi).

- [ ] **Step 5: Commit**

```
git add src/parser_v2/profiles/trader_a/signal_extractor.py src/parser_v2/tests/test_signal_extractor_patterns.py
git commit -m "feat(parser-v2): add RANGE entry structure detection in trader_a SignalExtractor"
```

---

## Task 3: Applicare RANGE a trader_b SignalExtractor

**Files:**
- Modify: `src/parser_v2/profiles/trader_b/signal_extractor.py`

- [ ] **Step 1: Verificare che trader_b abbia lo stesso problema**

```
pytest src/parser_v2/tests/test_signal_extractor_patterns.py -v -k "range"
```

I test appena aggiunti usano `trader_a.SignalExtractor` — passano già. Per trader_b non ci sono test equivalenti, ma il codice è identico quindi ha lo stesso gap.

- [ ] **Step 2: Applicare la stessa patch a trader_b**

Aprire `src/parser_v2/profiles/trader_b/signal_extractor.py`.

1. Trovare `_BULLET_CHARS = r"\-*•—–"` e aggiungere subito dopo:

```python
_ENTRY_RANGE_RE = re.compile(
    rf"\b(?:entry|vhod|{_CYR_ENTRY})\b"
    rf"(?:\s+(?:\([^)\n]*\)|[^\n:(){{}}]{{1,32}}))?"
    rf"\s*[:=@]?\s*"
    rf"(?P<min>{_NUMBER_PATTERN})\s*[-–—]\s*(?P<max>{_NUMBER_PATTERN})(?!\s*%)",
    re.IGNORECASE,
)
```

2. Alla fine del file aggiungere:

```python
def _try_range_entry(text: str) -> list[EntryLeg] | None:
    match = _ENTRY_RANGE_RE.search(text)
    if not match:
        return None
    min_price = _price_from_raw(match.group("min"))
    max_price = _price_from_raw(match.group("max"))
    if min_price is None or max_price is None:
        return None
    return [
        EntryLeg(sequence=1, entry_type="LIMIT", price=min_price, role="PRIMARY", is_optional=False),
        EntryLeg(sequence=2, entry_type="LIMIT", price=max_price, role="AVERAGING", is_optional=False),
    ]
```

3. Sostituire il metodo `extract()` con la stessa versione di trader_a (identica):

```python
def extract(self, normalized: NormalizedText, market_hint: bool = False) -> SignalDraft | None:
    text = normalized.raw_text
    normalized_text = normalized.normalized_text

    symbol = normalize_symbol(_extract_symbol(text))
    side = _extract_side(normalized_text)

    range_entries = _try_range_entry(text)
    if range_entries is not None:
        entries = range_entries
        entry_structure = "RANGE"
    else:
        entries = _extract_entries(text, market_hint=market_hint)
        entry_structure = _entry_structure(entries)

    stop_loss = _extract_stop_loss(text)
    take_profits = _extract_take_profits(text)
    risk_hint = _extract_risk_hint(text, self._risk_prefixes, self._risk_suffixes)

    if not any((entries, stop_loss, take_profits)):
        return None

    missing_fields = _missing_fields(
        symbol=symbol,
        side=side,
        entries=entries,
        stop_loss=stop_loss,
        take_profits=take_profits,
    )

    return SignalDraft(
        symbol=symbol,
        side=side,
        entry_structure=entry_structure,
        entries=entries,
        stop_loss=stop_loss,
        take_profits=take_profits,
        risk_hint=risk_hint,
        missing_fields=missing_fields,
        completeness="COMPLETE" if not missing_fields else "INCOMPLETE",
    )
```

- [ ] **Step 3: Aggiungere test RANGE per trader_b**

Aprire `src/parser_v2/tests/test_signal_extractor_patterns.py` e aggiungere in fondo:

```python
def test_trader_b_range_entry_produces_range_structure() -> None:
    from src.parser_v2.profiles.trader_b.signal_extractor import SignalExtractor as TraderBExtractor

    extractor = TraderBExtractor()
    text = "ETHUSDT.P Лонг\nВход: 2000-2100\nSL: 1900\nTP1: 2300\n"
    normalized = NormalizedText(raw_text=text, normalized_text=text.lower(), lines=text.splitlines())
    signal = extractor.extract(normalized)
    assert signal is not None
    assert signal.entry_structure == "RANGE"
    assert len(signal.entries) == 2
    assert signal.entries[0].price.value == 2000.0
    assert signal.entries[1].price.value == 2100.0
```

- [ ] **Step 4: Eseguire tutti i test**

```
pytest src/parser_v2/tests/ -v
```

Output atteso: tutti passano.

- [ ] **Step 5: Commit**

```
git add src/parser_v2/profiles/trader_b/signal_extractor.py src/parser_v2/tests/test_signal_extractor_patterns.py
git commit -m "feat(parser-v2): apply RANGE entry structure detection to trader_b SignalExtractor"
```

---

## Task 4: Test GAP A7 — no-mix `update.operations` / `targeted_actions`

**Files:**
- Modify: `src/parser_v2/tests/test_canonical_translator_v2.py`

- [ ] **Step 1: Aggiungere i test no-mix**

Aprire `src/parser_v2/tests/test_canonical_translator_v2.py` e aggiungere in fondo:

```python
def test_no_target_hints_uses_plain_operations_not_targeted() -> None:
    """Senza target hints, le operazioni vanno in update.operations, non targeted_actions."""
    intents = [_make_intent("MOVE_STOP_TO_BE")]
    parsed = _make_parsed(intents, target_hints=None)
    result = CanonicalTranslator().translate(parsed)

    assert len(result.targeted_actions) == 0
    assert result.update is not None
    assert len(result.update.operations) == 1
    assert result.update.operations[0].op_type == "SET_STOP"


def test_message_target_hints_forces_all_to_targeted_operations_empty() -> None:
    """Con target hints a livello messaggio, tutto in targeted_actions e update.operations vuoto."""
    intents = [_make_intent("MOVE_STOP_TO_BE"), _make_intent("CANCEL_PENDING")]
    parsed = _make_parsed(intents, target_hints=TargetHints(telegram_message_ids=[99]))
    result = CanonicalTranslator().translate(parsed)

    assert len(result.targeted_actions) == 2
    assert result.update is not None
    assert len(result.update.operations) == 0


def test_per_intent_local_target_forces_all_to_targeted_no_mix() -> None:
    """Se almeno un intent ha target locale, tutti vanno in targeted_actions — anche quelli senza."""
    local_hints = TargetHints(target_source="LOCAL_TEXT_LINK", telegram_message_ids=[11])
    intents = [
        _make_intent("MOVE_STOP_TO_BE", target_hints=local_hints),
        _make_intent("CANCEL_PENDING"),  # nessun target locale
    ]
    parsed = _make_parsed(intents, target_hints=None)
    result = CanonicalTranslator().translate(parsed)

    assert len(result.targeted_actions) == 2
    assert result.update is not None
    assert len(result.update.operations) == 0
    action_types = {a.action_type for a in result.targeted_actions}
    assert "SET_STOP" in action_types
    assert "CANCEL_PENDING" in action_types
```

- [ ] **Step 2: Eseguire i nuovi test**

```
pytest src/parser_v2/tests/test_canonical_translator_v2.py -v -k "no_mix or plain_operations or local_target"
```

Output atteso: tutti e 3 i nuovi test passano (la logica è già implementata).

- [ ] **Step 3: Eseguire l'intera suite**

```
pytest src/parser_v2/tests/ -v
```

Output atteso: tutti i test passano.

- [ ] **Step 4: Commit**

```
git add src/parser_v2/tests/test_canonical_translator_v2.py
git commit -m "test(parser-v2): add explicit no-mix coverage for GAP A7 targeted_actions vs update.operations"
```

---

## Task 5: Round-trip su dati reali + aggiornamento AUDIT.md

**Files:**
- Read-only: `db/live.db`
- Modify: `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/AUDIT.md`

- [ ] **Step 1: Eseguire il replay su trader_a**

```
python parser_test/scripts/replay_parser_v2.py --db-path db/live.db --trader-filter trader_a --force-reparse
```

Output atteso: nessun crash, messaggio finale con conteggio messaggi processati.

- [ ] **Step 2: Verificare zero errori di schema tramite query diretta sul DB**

```
python -c "
import sqlite3
conn = sqlite3.connect('db/live.db')
rows = conn.execute(
    \"SELECT COUNT(*) as total, SUM(CASE WHEN error_status != 'OK' THEN 1 ELSE 0 END) as errors \"
    \"FROM parser_results_v2 pr \"
    \"JOIN raw_messages rm ON pr.raw_message_id = rm.raw_message_id \"
    \"WHERE rm.resolved_trader_id = 'trader_a'\"
).fetchone()
conn.close()
print(f'Total: {rows[0]}, Errors: {rows[1]}')
"
```

Output atteso: `Errors: 0`

- [ ] **Step 3: Generare il CSV di report e l'audit CSV**

```
python parser_test/scripts/generate_parser_reports_v2.py --db-path db/live.db --trader-filter trader_a
python parser_test/scripts/replay_parser_v2.py --db-path db/live.db --trader-filter trader_a --audit-csv
```

Verificare che il CSV sia generato e leggibile (almeno le colonne: `raw_message_id`, `primary_class`, `parse_status`, `primary_intent`, `warnings`).

- [ ] **Step 4: Aggiornare AUDIT.md**

Aprire `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/AUDIT.md`.

Nella sezione "Gap per fase — stato attuale", aggiornare:

**Fase 5 — SignalExtractor:**
```markdown
### Fase 5 — SignalExtractor (100%) ✅ chiuso
- ~~Gap residuo: struttura `RANGE` non implementata~~ → risolto 2026-05-13
  - Pattern `_ENTRY_RANGE_RE` aggiunto in trader_a e trader_b
  - `_try_range_entry()` rileva formato `entry: N-M` prima degli altri pattern
  - Test: `test_range_entry_format_produces_range_structure`, `test_range_entry_english_format`
```

Nella sezione "Rischi aperti", aggiornare la riga RANGE:
```markdown
| Struttura `RANGE` non implementata | ~~Bassa~~ | ~~Rara nel dataset Trader A; produce TWO_STEP~~ | **CHIUSO 2026-05-13** |
```

Aggiungere una sezione "Decisioni backlog confermate" se non esiste:
```markdown
## Decisioni backlog confermate — 2026-05-13

### Multi-ref grouping (Fase 11 — CanonicalTranslator 90%)
**Decisione:** accettato come stabile. Il fallback `PARTIAL + warning` è corretto e non produce errori di schema.
**Motivazione:** il codice è funzionale; i casi multi-ref con intento omogeneo sono rari nel dataset attivo.
**Priorità backlog:** bassa. Rivalutare se multi-ref diventa frequente nei dati reali.

### Round-trip su dati reali trader_a
**Data:** 2026-05-13
**Risultato:** zero `error_status=ERROR` su tutti i messaggi trader_a in `db/live.db`.
**Parser pronto per integrazione in runtime_v2 (PRD 2.b).**
```

- [ ] **Step 5: Commit finale PRD 2.a**

```
git add src/parser_v2/docs/PARSER_DA_ZERO_DOCS/AUDIT.md
git commit -m "docs(parser-v2): close RANGE gap and round-trip verification — PRD 2.a complete"
```

---

## Verifica finale

```
pytest src/parser_v2/tests/ -v
```

Output atteso: tutti i test passano (≥ 94 pre-esistenti + nuovi).

**PRD 2.a è done.** Procedere con PRD 2.b (`docs/superpowers/plans/2026-05-13-prd2b-parser-pipeline-integration.md`).
