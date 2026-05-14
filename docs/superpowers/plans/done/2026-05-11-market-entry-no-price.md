# MARKET Entry Without Price — Fix & Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Garantire che tutti i path di estrazione entry in trader_a e trader_b producano `EntryLeg(entry_type="MARKET", price=None)` quando il marker MARKET è presente ma nessun prezzo numerico è nel testo, allineando il comportamento al contratto canonico (`canonical_v1/models.py:209`).

**Architecture:** Il contratto `EntryLeg` dichiara `price: Price | None = None` e il validator richiede price solo per LIMIT. Il bug è in `trader_a/extractors.py` dove `_ENTRY_CURRENT_RE` cattura marker+prezzo insieme: se il prezzo manca il regex non matcha e si ricade nel path LIMIT. I path canonical di trader_a e trader_b sono già corretti ma privi di test. Il fix separa la detection del marker MARKET dall'estrazione del prezzo.

**Tech Stack:** Python 3.12, Pydantic v2, pytest, `re` stdlib

---

## File Map

| File | Azione | Motivo |
|---|---|---|
| `src/parser/trader_profiles/trader_a/extractors.py` | Modifica | Fix bug `_extract_entries()` — aggiunge `_MARKET_MARKER_ONLY_RE`, separa marker detection da price capture |
| `src/parser/trader_profiles/trader_a/tests/test_extractors_market.py` | Crea | Unit test su `_extract_entries()` direttamente |
| `src/parser/trader_profiles/trader_a/tests/test_canonical_output.py` | Modifica | Aggiunge test canonical MARKET senza prezzo |
| `src/parser/trader_profiles/trader_b/tests/test_canonical_output.py` | Modifica | Aggiunge test canonical MARKET senza prezzo |

---

## Task 1: Fix `_extract_entries()` in `trader_a/extractors.py`

**Files:**
- Modify: `src/parser/trader_profiles/trader_a/extractors.py`

Il problema è in due punti della funzione `_extract_entries` (righe 284-335):

**Punto A — main path (righe 308-324):** `_ENTRY_CURRENT_RE` richiede un numero dopo "вход с текущих". Se il numero manca, `primary = None`, poi il blocco `if primary is None` prova il regex LIMIT e sovrascrive `primary_type = "LIMIT"`.

**Punto B — AB path (riga 294):** `if price is None: continue` salta qualsiasi leg senza prezzo, anche MARKET. In pratica `_ENTRY_AB_RE` richiede già un numero, ma la guardia è semanticamente sbagliata.

- [ ] **Step 1: Aggiungi `_MARKET_MARKER_ONLY_RE` a livello modulo**

Apri `src/parser/trader_profiles/trader_a/extractors.py`. Dopo la riga che definisce `_ENTRY_CURRENT_RE` (circa riga 38), aggiungi:

```python
_MARKET_MARKER_ONLY_RE = re.compile(r"вход\s+с\s+текущих", re.IGNORECASE)
```

- [ ] **Step 2: Correggi il main path in `_extract_entries()`**

Sostituisci il blocco righe 308-324 (da `primary = _search_price(_ENTRY_CURRENT_RE, text)` fino alla fine dell'`if primary is not None:`) con:

```python
    primary = _search_price(_ENTRY_CURRENT_RE, text)
    has_market_marker = bool(_MARKET_MARKER_ONLY_RE.search(text))

    if primary is not None:
        primary_type: str = "MARKET"
    elif has_market_marker:
        primary_type = "MARKET"
        # primary rimane None — entry at market, nessun livello numerico specificato
    else:
        primary = _search_price(_ENTRY_LIMIT_RE, text) or _search_price(_ENTRY_SIMPLE_RE, text)
        primary_type = "LIMIT"

    averaging = _search_price(_AVERAGING_RE, text)

    if primary is not None or has_market_marker:
        entries.append(
            EntryLeg(
                sequence=1,
                entry_type=primary_type,  # type: ignore[arg-type]
                price=primary,
                role="PRIMARY",
                is_optional=False,
            )
        )
```

- [ ] **Step 3: Correggi il guard nell'AB path**

Nella sezione AB (circa riga 294), cambia:

```python
            if price is None:
                continue
```

in:

```python
            if price is None and entry_type == "LIMIT":
                continue
```

Questo permette leg MARKET senza prezzo anche nello stile A/B.

---

## Task 2: Unit test su `_extract_entries()` — crea `test_extractors_market.py`

**Files:**
- Create: `src/parser/trader_profiles/trader_a/tests/test_extractors_market.py`

- [ ] **Step 1: Scrivi i test che devono fallire (TDD)**

Crea il file con questo contenuto:

```python
"""Unit tests for _extract_entries() in trader_a/extractors.py — MARKET entry constraint."""
from __future__ import annotations

import pytest

from src.parser.trader_profiles.trader_a.extractors import _extract_entries


class TestExtractEntriesMarket:
    def test_market_with_price_creates_market_leg(self) -> None:
        entries = _extract_entries("вход с текущих 90000")
        assert len(entries) == 1
        assert entries[0].entry_type == "MARKET"
        assert entries[0].price is not None
        assert entries[0].price.value == 90000.0

    def test_market_without_price_creates_market_leg_price_none(self) -> None:
        """Bug fix: MARKET marker senza numero non deve cadere nel path LIMIT."""
        entries = _extract_entries("вход с текущих")
        assert len(entries) == 1
        assert entries[0].entry_type == "MARKET"
        assert entries[0].price is None

    def test_market_without_price_in_signal_context(self) -> None:
        """MARKET marker con sl/tp ma senza prezzo entry."""
        entries = _extract_entries("вход с текущих\nSL: 89000\nTP1: 93000")
        assert len(entries) == 1
        assert entries[0].entry_type == "MARKET"
        assert entries[0].price is None

    def test_market_marker_does_not_override_with_limit_detection(self) -> None:
        """Quando MARKET marker è presente, primary_type non deve diventare LIMIT."""
        entries = _extract_entries("вход с текущих sl: 89000")
        assert len(entries) == 1
        assert entries[0].entry_type == "MARKET"

    def test_limit_with_price_creates_limit_leg(self) -> None:
        entries = _extract_entries("entry: 90000")
        assert len(entries) == 1
        assert entries[0].entry_type == "LIMIT"
        assert entries[0].price is not None
        assert entries[0].price.value == 90000.0

    def test_no_entry_marker_returns_empty(self) -> None:
        entries = _extract_entries("SL: 89000 TP1: 93000")
        assert len(entries) == 0

    def test_market_leg_sequence_is_1(self) -> None:
        entries = _extract_entries("вход с текущих")
        assert entries[0].sequence == 1

    def test_market_leg_role_is_primary(self) -> None:
        entries = _extract_entries("вход с текущих")
        assert entries[0].role == "PRIMARY"

    def test_market_leg_is_not_optional(self) -> None:
        entries = _extract_entries("вход с текущих")
        assert entries[0].is_optional is False
```

- [ ] **Step 2: Esegui i test — verifica che falliscano**

```
pytest src/parser/trader_profiles/trader_a/tests/test_extractors_market.py -v
```

Expected: `test_market_without_price_creates_market_leg_price_none` e `test_market_without_price_in_signal_context` e `test_market_marker_does_not_override_with_limit_detection` FAIL. Gli altri passano già.

- [ ] **Step 3: Applica il fix (Task 1)**

Esegui il Task 1 se non già fatto.

- [ ] **Step 4: Riesegui i test — verifica che passino tutti**

```
pytest src/parser/trader_profiles/trader_a/tests/test_extractors_market.py -v
```

Expected: tutti PASS.

- [ ] **Step 5: Commit**

```
git add src/parser/trader_profiles/trader_a/extractors.py src/parser/trader_profiles/trader_a/tests/test_extractors_market.py
git commit -m "fix(trader_a): MARKET entry without price creates EntryLeg(price=None) in extractors"
```

---

## Task 3: Test canonical trader_a — MARKET senza prezzo

**Files:**
- Modify: `src/parser/trader_profiles/trader_a/tests/test_canonical_output.py`

Il canonical path di trader_a (`parse_canonical` → `_extract_signal_entry_plan`) gestisce già MARKET senza prezzo correttamente in teoria. Questo task aggiunge il test di regressione esplicito.

- [ ] **Step 1: Scrivi il test che deve passare**

Apri `src/parser/trader_profiles/trader_a/tests/test_canonical_output.py`. Individua la classe `TestTraderACanonicalSignal` (già esistente, ha `test_signal_complete` e `test_setup_incomplete_emits_partial_signal`). Aggiungi questi metodi alla classe:

```python
    def test_market_signal_without_price_produces_market_leg(self) -> None:
        """MARKET entry senza prezzo numerico — price must be None, entry_type MARKET."""
        text = "BTCUSDT long\nвход с текущих\nSL: 89000\nTP1: 93000"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "SIGNAL")
        assert msg.signal is not None
        self.assertEqual(len(msg.signal.entries), 1)
        leg = msg.signal.entries[0]
        self.assertEqual(leg.entry_type, "MARKET")
        self.assertIsNone(leg.price)

    def test_market_signal_without_price_entry_structure_one_shot(self) -> None:
        text = "BTCUSDT long\nвход с текущих\nSL: 89000\nTP1: 93000"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        assert msg.signal is not None
        self.assertEqual(msg.signal.entry_structure, "ONE_SHOT")

    def test_market_signal_with_price_still_works(self) -> None:
        """Regressione: MARKET con prezzo indicativo deve funzionare come prima."""
        text = "BTCUSDT long\nвход с текущих 90000\nSL: 89000\nTP1: 93000"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        assert msg.signal is not None
        self.assertEqual(len(msg.signal.entries), 1)
        leg = msg.signal.entries[0]
        self.assertEqual(leg.entry_type, "MARKET")
        self.assertIsNotNone(leg.price)
        self.assertAlmostEqual(leg.price.value, 90000.0)
```

- [ ] **Step 2: Esegui i test**

```
pytest src/parser/trader_profiles/trader_a/tests/test_canonical_output.py -v
```

Expected: tutti PASS (il canonical path è già corretto — se falliscono, c'è un bug in `_extract_signal_entry_plan` da investigare prima di procedere).

- [ ] **Step 3: Commit**

```
git add src/parser/trader_profiles/trader_a/tests/test_canonical_output.py
git commit -m "test(trader_a): add MARKET entry without price canonical regression tests"
```

---

## Task 4: Test canonical trader_b — MARKET senza prezzo

**Files:**
- Modify: `src/parser/trader_profiles/trader_b/tests/test_canonical_output.py`

Il canonical path di trader_b è già corretto (`_build_tb_signal_payload` usa `else: entries = [EntryLeg(sequence=1, entry_type="MARKET", role="PRIMARY")]`). Questo task aggiunge il test di regressione esplicito.

- [ ] **Step 1: Scrivi il test**

Apri `src/parser/trader_profiles/trader_b/tests/test_canonical_output.py`. Individua la classe `TestTraderBCanonicalNewSignal` (già esistente). Aggiungi:

```python
    def test_market_signal_without_price_produces_market_leg(self) -> None:
        """MARKET entry senza prezzo numerico — price must be None, entry_type MARKET."""
        text = "$BTCUSDT - Лонг\nвход с текущих\nСтоп лосс: 89000\nТП1: 93000"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        self.assertEqual(msg.primary_class, "SIGNAL")
        assert msg.signal is not None
        self.assertEqual(len(msg.signal.entries), 1)
        leg = msg.signal.entries[0]
        self.assertEqual(leg.entry_type, "MARKET")
        self.assertIsNone(leg.price)

    def test_market_signal_without_price_entry_structure_one_shot(self) -> None:
        text = "$BTCUSDT - Лонг\nвход с текущих\nСтоп лосс: 89000\nТП1: 93000"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        assert msg.signal is not None
        self.assertEqual(msg.signal.entry_structure, "ONE_SHOT")

    def test_market_via_po_tekushim_marker_no_price(self) -> None:
        """Variante marker 'по текущим' senza prezzo — stesso contratto."""
        text = "$ETHUSDT - Лонг\nВход по текущим\nСтоп лосс: 3100\nТП1: 3300"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        assert msg.signal is not None
        self.assertEqual(len(msg.signal.entries), 1)
        leg = msg.signal.entries[0]
        self.assertEqual(leg.entry_type, "MARKET")
        self.assertIsNone(leg.price)

    def test_market_signal_with_price_still_works(self) -> None:
        """Regressione: MARKET con prezzo indicativo deve funzionare come prima."""
        text = "$BTCUSDT - Лонг\nвход с текущих: 90000\nСтоп лосс: 89000\nТП1: 93000"
        msg = self.parser.parse_canonical(text, _ctx(text=text))
        assert msg.signal is not None
        leg = msg.signal.entries[0]
        self.assertEqual(leg.entry_type, "MARKET")
        self.assertIsNotNone(leg.price)
        self.assertAlmostEqual(leg.price.value, 90000.0)
```

- [ ] **Step 2: Esegui i test**

```
pytest src/parser/trader_profiles/trader_b/tests/test_canonical_output.py -v
```

Expected: tutti PASS.

- [ ] **Step 3: Commit**

```
git add src/parser/trader_profiles/trader_b/tests/test_canonical_output.py
git commit -m "test(trader_b): add MARKET entry without price canonical regression tests"
```

---

## Task 5: Smoke run completo

- [ ] **Step 1: Esegui tutta la suite trader_a**

```
pytest src/parser/trader_profiles/trader_a/tests/ -v
```

Expected: tutti PASS. Se fallisce qualcosa di pre-esistente, NON è causato da questa feature — investigare separatamente.

- [ ] **Step 2: Esegui tutta la suite trader_b**

```
pytest src/parser/trader_profiles/trader_b/tests/ -v
```

Expected: tutti PASS.

- [ ] **Step 3: Esegui i test del modello canonico**

```
pytest tests/parser_canonical_v1/ -v
```

Expected: tutti PASS — il modello `EntryLeg` non è stato modificato.

- [ ] **Step 4: Commit finale se tutto passa**

```
git add .
git commit -m "chore: verify all trader_a and trader_b tests pass after MARKET fix"
```
