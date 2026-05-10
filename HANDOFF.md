# Handoff — 2026-05-10

## Cosa è stato fatto

Refactor completo della gestione `MODIFY_ENTRY` in `parser_v2` per il profilo `trader_a`.

### Cambiamenti principali

**1. Mode detection da evidence (non regex)**
I regex `_RE_MARKET_NOW` / `_RE_REMOVE` rimossi da `intent_entity_extractor.py`. Il mode ora viene da `MarkerEvidence` con `kind="modify_entry_mode"` — coerente con il resto del sistema.

**2. Entry selector come primo cittadino**
Aggiunto `entry_selector_markers` come nuovo `MarkerKind` wireed attraverso `MarkerMatcher`. Il parser distingue `PRIMARY` vs `AVERAGING` dal testo.

**3. Range e ladder**
`_modify_entry_entities` supporta:
- Prezzo singolo → `ONE_SHOT`
- `2114-2120` → `RANGE` + upgrade automatico mode a `UPDATE_RANGE`
- `2114 2100 2080` → `LADDER`

**4. Context window**
L'estrazione prezzi si ferma allo start del prossimo intent marker — previene cross-intent contamination.

**5. Contratti estesi**
- `EntrySelector(role, sequence, label, raw)` — nuovo modello in `entities.py`
- `ModifyEntryEntities` — aggiunto `entry_selector`, `entry_structure`, `raw_selector_marker`
- `ModifyEntriesOperation` — aggiunto `entry_selector`
- `ModifyEntryMode` — aggiunto `UPDATE_RANGE`, `REPLACE_ENTRY`
- `ModifyEntriesOperationKind` — aggiunto `UPDATE_RANGE`, `REPLACE_ENTRY`

---

## File toccati

```
src/parser_v2/contracts/enums.py
src/parser_v2/contracts/entities.py
src/parser_v2/contracts/canonical_message.py
src/parser_v2/contracts/rules.py
src/parser_v2/core/marker_matcher.py
src/parser_v2/profiles/trader_a/semantic_markers.json
src/parser_v2/profiles/trader_a/intent_entity_extractor.py
src/parser_v2/translation/canonical_translator.py
src/parser_v2/tests/test_modify_entry_extractor.py        ← nuovo
src/parser_v2/tests/test_canonical_translator_v2.py
src/parser_v2/tests/test_contracts_parsed_intent.py
src/parser_v2/tests/test_contracts_rules.py
docs/AUDIT.md
src/parser_v2/README.md
```

---

## Stato attuale del sistema

```
pytest src/parser_v2/tests/ → 115 passed ✅
```

Tutti gli invarianti rispettati: `ADD_ENTRY`/`REENTER` invariati, `REMOVE` legacy compatibile, nessuna regressione.

---

## Rischi e TODO aperti

**1. MARKER REVIEW RICHIESTA (priorità alta)**
Il contenuto di `entry_selector_markers` e `modify_entry_mode_markers` in `semantic_markers.json` è da validare su dati reali. I marker attuali sono derivati dal PRD, non da un replay del corpus trader_a. Esegui:

```bash
python parser_test/scripts/replay_parser_v2.py --trader trader_a
```

Controlla nei CSV i messaggi `UPDATE/MODIFY_ENTRY` e verifica che mode e selector siano corretti. Aggiorna poi i JSON di conseguenza.

**2. Edge case non testato**
`UPDATE_RANGE` da marker esplicito + 3 prezzi sciolti → `entry_structure=LADDER` (mode/structure incoerenti). Non è un bug ma non è testato — valuta se è un caso reale nel corpus.

**3. Nuovi mode non verificati su dati reali**
`UPDATE_RANGE` (`"диапазон входа"`) e `REPLACE_ENTRY` (`"заменяем вход"`) sono nei marker ma non è verificato che trader_a usi queste frasi. Conferma con i dati.

---

## Prossimo prompt consigliato

```
Esegui il replay di trader_a sui messaggi MODIFY_ENTRY e verifica che mode 
e selector siano rilevati correttamente. Aggiorna semantic_markers.json 
con i marker reali e porta i test a coprire i casi trovati.

File di partenza:
- parser_test/scripts/replay_parser_v2.py
- src/parser_v2/profiles/trader_a/semantic_markers.json
  (sezioni: modify_entry_mode_markers, entry_selector_markers)
- src/parser_v2/tests/test_modify_entry_extractor.py

Dopo la review marker, aggiungi test di integrazione end-to-end
(testo grezzo → CanonicalMessage) per i casi MODIFY_ENTRY trovati nel corpus reale.
```
