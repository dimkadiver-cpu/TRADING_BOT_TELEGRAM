# PRD 2.a — Parser V2: Gap Closure e Verifica su Dati Reali

**Data:** 2026-05-13
**Stato:** draft
**Deriva da:** `docs/Raggionamento/documento_madre_riprogettazione_trading_bot_telegram_v_0_1.md` v0.2, Fase C
**Precondizione:** nessuna — PRD 2.a è il primo passo del blocco parser
**Sblocca:** PRD 2.b (integrazione pipeline runtime_v2)

---

## 1. Scopo

Blindare `parser_v2` prima che venga integrato nel live. Questo PRD non tocca `runtime_v2`. Alla chiusura, `parser_v2` deve essere verificato su dati reali e i gap noti risolti o documentati con decisione esplicita.

**Trader in scope:** trader_a (canale attivo), trader_b (profilo esistente, canale inattivo).
**Fuori scope:** trader_c, trader_d, trader_3 — profili da sviluppare in PRD separati quando i canali vengono attivati.

---

## 2. Stato di partenza

`parser_v2` è funzionante con 94/94 test passanti. I contratti sono stabili (GAPs A1-A8 tutti applicati). I gap residui identificati nel codebase sono:

| Gap | File | Severità | Stato |
|---|---|---|---|
| RANGE entry structure mai prodotta | `profiles/trader_a/signal_extractor.py` | Bassa | Confermato nel codice |
| Test copertura GAP A7 (no-mix) | `tests/parser_v2/test_canonical_translator_v2.py` | Media | Logica OK, zero test espliciti |
| Round-trip su dati reali trader_a | — | Alta | Mai eseguito |
| Multi-ref grouping CanonicalTranslator | `translation/canonical_translator.py` | Bassa | Accettato come backlog |

---

## 3. Gap da chiudere

### 3.1 RANGE entry structure

**Problema:** `_entry_structure()` in `signal_extractor.py` non distingue tra due entry discrete (TWO_STEP) e una zona di entrata (RANGE). Per 2 leg restituisce sempre `TWO_STEP`.

**Comportamento attuale:**
```python
def _entry_structure(entries: list[EntryLeg]) -> str | None:
    if len(entries) >= 3: return "LADDER"
    if len(entries) == 2: return "TWO_STEP"   # mai RANGE
    if len(entries) == 1: return "ONE_SHOT"
    return None
```

**Soluzione:** aggiungere riconoscimento del formato `<number>-<number>` nella sezione entry. Se il pattern corrisponde a una zona, produrre 2 leg LIMIT con `entry_structure=RANGE`.

**Criterio done:**
- `entry: 2110-2120` → `entry_structure=RANGE`, 2 leg LIMIT (min=2110, max=2120)
- `entry a: 2110 / entry b: 2120` → `entry_structure=TWO_STEP`
- Test unitario per entrambi i casi

**Applica a:** trader_a, trader_b (stessa `SignalExtractor`).

---

### 3.2 Test copertura GAP A7 — no-mix `update.operations` / `targeted_actions`

**Problema:** la regola "se esiste anche una sola azione con target esplicito, tutte le operazioni vanno in `targeted_actions`" è implementata correttamente in `CanonicalTranslator` ma non ha test espliciti.

**Casi da coprire in `test_canonical_translator_v2.py`:**

1. UPDATE con `target_hints` a livello messaggio → tutte le operazioni in `targeted_actions`, `update.operations` vuoto
2. UPDATE senza `target_hints` → tutte le operazioni in `update.operations`, `targeted_actions` vuoto
3. UPDATE con almeno un intent con `target_hints` locale → tutte in `targeted_actions` (anche quelli senza target locale)

---

### 3.3 Multi-ref grouping — decisione accettazione backlog

**Decisione:** il comportamento attuale (fallback `PARTIAL + warning` per casi multi-ref complessi) è accettato come stabile. Non si implementa grouping semantico in questo PRD.

**Motivazione:** il codice è funzionale, non produce errori di schema, e i casi multi-ref con intento omogeneo sono rari nel dataset trader_a. Il rischio di regressione supera il beneficio.

**Azione:** aggiornare `AUDIT.md` con questa decisione esplicita e la priorità backlog bassa.

---

## 4. Round-trip su dati reali

**Obiettivo:** verificare che `parser_v2` su messaggi reali trader_a produca `CanonicalMessage` validi senza errori di schema prima dell'integrazione in runtime_v2.

**Strumento:** `parser_test/scripts/replay_parser_v2.py` — già funzionante, usa `UniversalParserRuntime`, persiste in `parser_results_v2`.

**Flusso:**
```
db/live.db (raw_messages trader_a)
      ↓
replay_parser_v2.py --trader-filter trader_a
      ↓
parser_results_v2 (canonical_json, parse_status, warnings)
      ↓
generate_parser_reports_v2.py → CSV
      ↓
analisi output + verifica soglie
```

**Soglie minime:**

| Metrica | Soglia |
|---|---|
| Messaggi con `error_status=ERROR` | 0 — zero crash Pydantic |
| `parse_status=PARSED` su messaggi operativi attesi | ≥ 70% |
| Warning `canonical_translation_without_update_operation` | 0 su messaggi con intent UPDATE |
| RANGE prodotto dove formato zona-entrata presente | verificato manualmente sui casi noti |

**Nota:** le soglie numeriche sono indicative. Il criterio primario è zero errori di schema e nessuna regressione rispetto a run precedenti se esistono.

---

## 5. Acceptance criteria

PRD 2.a è done quando:

1. `_entry_structure()` produce `RANGE` per formato zona-entrata e `TWO_STEP` per due entry discrete. Test unitario per entrambi i casi verde.

2. Test espliciti per GAP A7 (no-mix) aggiunti e verdi: UPDATE con target hints → tutto in `targeted_actions`; UPDATE senza → tutto in `update.operations`; UPDATE con intent misto → tutto in `targeted_actions`.

3. Replay su `db/live.db` con `--trader-filter trader_a` completa senza `error_status=ERROR` su nessun messaggio.

4. CSV di report generato e leggibile (campi minimi: `raw_message_id`, `primary_class`, `parse_status`, `warnings`, `primary_intent`).

5. Tutti i test esistenti `src/parser_v2/tests/` restano verdi (94/94 + nuovi).

6. `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/AUDIT.md` aggiornato con gap chiusi e decisione esplicita multi-ref grouping come backlog.

**Segnale primario:** zero errori di schema Pydantic sul replay dati reali trader_a.

---

## 6. File toccati

| File | Tipo modifica |
|---|---|
| `src/parser_v2/profiles/trader_a/signal_extractor.py` | Fix: RANGE entry structure |
| `src/parser_v2/profiles/trader_b/signal_extractor.py` | Fix: RANGE entry structure (stessa logica) |
| `src/parser_v2/tests/test_canonical_translator_v2.py` | Aggiunta test GAP A7 |
| `src/parser_v2/tests/test_signal_extractor_patterns.py` | Aggiunta test RANGE |
| `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/AUDIT.md` | Aggiornamento gap/decisioni |

**Non toccati:** `runtime_v2/`, `src/telegram/router.py`, `src/parser/`, contratti `canonical_message.py`.

---

## 7. Rischi

| Rischio | Mitigazione |
|---|---|
| RANGE pattern ambiguo con prezzi range SL/TP | Pattern limitato alla sezione entry del testo, non globale |
| Replay su live.db rivela regressioni non previste | Fix dedicati prima di procedere a PRD 2.b |
| trader_b ha `signal_extractor.py` separato | Applicare stesso fix, verificare con test specifici |
