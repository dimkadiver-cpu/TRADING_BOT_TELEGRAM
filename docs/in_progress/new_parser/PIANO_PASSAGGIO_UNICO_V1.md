# PIANO DI PASSAGGIO UNICO — Canonical Parser Model v1

> Data redazione: 2026-04-22
> Stato: DEFINITIVO — Fasi 1-8 completate (trader_3, trader_b, trader_c, trader_d, trader_a migrati)

---

## Decisioni risolte

| # | Decisione | Scelta |
|---|-----------|--------|
| 1 | `Price/normalize_price` | Copiati in `canonical_v1/models.py`, `PriceValue` rinominato in `Price` |
| 2 | `CloseOperation` CLOSE full senza scope | Responsabilità al normalizer: setta sempre `close_scope="FULL"` quando intent è `U_CLOSE_FULL` senza scope. Modello resta strict. |
| 3 | Intent orfani | Mappati al tipo più vicino: `U_ACTIVATION`/`U_MARK_FILLED` → `REPORT/ENTRY_FILLED`, `U_INVALIDATE_SETUP` → `UPDATE/CANCEL_PENDING`, `U_REVERSE_SIGNAL` → `UPDATE/CLOSE` only (parte new signal ignorata), `U_RISK_NOTE` → `INFO` |
| 4 | `U_EXIT_BE` | `REPORT / BREAKEVEN_EXIT` — è un evento passato, non un ordine |
| 5 | Storage shadow mode | Tabella separata `parse_results_v1` con migration DB |
| 6 | Chi scrive le migration DB | Claude Code scrive i file SQL, utente rivede e applica. CLAUDE.md va aggiornato per autorizzare. |

---

## Stato reale del codebase (ricognizione fatta ora)

### Contratti esistenti

| File | Tipo | Stato |
|------|------|-------|
| `src/parser/trader_profiles/base.py::TraderParseResult` | dataclass | **IN USO** — output attuale di tutti i profili |
| `src/parser/action_builders/canonical_v2.py` | module | **IN USO** — normalizer implicito, 600+ righe di domain logic |
| `src/parser/models/canonical.py::TraderParseResult` | Pydantic | **LEGACY ORFANO** — non usato dai profili, solo in alcuni test |
| `src/parser/models/new_signal.py`, `update.py`, `operational.py` | Pydantic | **DA VERIFICARE** se usati |
| `docs/in_progress/new_parser/canonical_parser_model_v1.py::CanonicalMessage` | Pydantic | **TARGET** — congelato e definitivo |

### Flusso attuale reale (non documentato)

```
Telegram message
    ↓
ParserContext + testo
    ↓
trader_X/profile.py          → classifica + estrae intents + entities (dict)
    ↓
TraderParseResult (dataclass) → base.py
    ↓
canonical_v2.py::build_actions_structured()  → converte intents in actions_structured (dict list)
    ↓
TraderParseResult con .actions_structured    → consumato dal router
```

### Flusso target

```
Telegram message
    ↓
ParserContext + testo
    ↓
trader_X/profile.py          → classifica + estrae grezzi (TraderRawExtraction)
    ↓
CanonicalNormalizer           → converte in CanonicalMessage v1
    ↓
CanonicalMessage              → router, operation_rules
```

---

## Cosa è `canonical_v2.py` (chiarito)

E il **normalizer implicito attuale**. Contiene domain knowledge reale e testato:
- mapping `intent → action_type` (20+ intent)
- normalizzazione stop levels (`ENTRY` / `BREAKEVEN` / `TP_LEVEL` / prezzo)
- normalizzazione close scopes
- normalizzazione cancel scopes
- estrazione targeting da Telegram links
- line-level stop refinement

**Non va buttato.** La business logic al suo interno diventa la base del `CanonicalNormalizer` v1.

---

## Fasi del piano

### FASE 1 — Pulizia e posizionamento del contratto

**Obiettivo:** un solo file sorgente ufficiale del modello, in `src/`.

**Attivita:**
1. Spostare `docs/in_progress/new_parser/canonical_parser_model_v1.py` → `src/parser/canonical_v1/models.py`
2. Creare `src/parser/canonical_v1/__init__.py`
3. Marcare `src/parser/models/canonical.py` come `# LEGACY — non usare in nuovo codice` (non eliminare ancora: ha i test price_normalization che girano)
4. Marcare `src/parser/models/new_signal.py`, `update.py`, `operational.py` idem

**Exit criteria:** `from src.parser.canonical_v1.models import CanonicalMessage` funziona.

**NON fare:** non toccare `base.py`, `canonical_v2.py`, nessun profilo.

> ✅ **Decisione #1 — RISOLTA:** `Price` + `normalize_price()` vanno copiati in `canonical_v1/models.py`. `PriceValue` viene rinominato `Price`. Un solo posto, tutto insieme.

---

### FASE 2 — Fixture canoniche e test dello schema

**Obiettivo:** verificare che il modello v1 sia corretto prima di collegarlo a qualsiasi parser.

**Attivita:**
1. Creare cartella `tests/parser_canonical_v1/`
2. Creare fixture Python per:
   - `SIGNAL ONE_SHOT` completo (con stop, TP, entry MARKET)
   - `SIGNAL TWO_STEP` (due entry LIMIT)
   - `SIGNAL RANGE` (due prezzi min/max)
   - `SIGNAL LADDER` (3+ entry)
   - `SIGNAL PARTIAL` (mancano campi, parse_status=PARTIAL)
   - `UPDATE SET_STOP` a prezzo
   - `UPDATE SET_STOP` a ENTRY (breakeven)
   - `UPDATE SET_STOP` a TP_LEVEL
   - `UPDATE CLOSE` full
   - `UPDATE CLOSE` partial con fraction
   - `UPDATE CANCEL_PENDING`
   - `UPDATE MODIFY_ENTRIES ADD`
   - `UPDATE MODIFY_TARGETS REPLACE_ALL`
   - `REPORT TP_HIT`
   - `REPORT STOP_HIT`
   - `REPORT FINAL_RESULT`
   - `UPDATE + REPORT` composito
   - `INFO` puro
3. Creare `test_canonical_v1_schema.py` — casi positivi + negativi hard

**Casi negativi obbligatori:**
- `SIGNAL` senza `signal` payload → deve fallire
- `SIGNAL` con `update` presente → deve fallire
- `ONE_SHOT` con 0 entry legs → deve fallire
- `TWO_STEP` con 1 leg → deve fallire
- `LADDER` con 2 legs → deve fallire
- `LIMIT` leg senza price → deve fallire
- `SET_STOP` con due sub-fields popolati → deve fallire
- `INFO` con `signal` presente → deve fallire

> ✅ **Decisione #2 — RISOLTA:** Modello resta strict. Il normalizer setta sempre `close_scope="FULL"` quando l'intent è `U_CLOSE_FULL` e il profilo non fornisce scope. Stessa logica già presente in `canonical_v2.py` riga 344-345.

**Exit criteria:** tutti i casi positivi passano, tutti i negativi falliscono con `ValidationError`.

---

### FASE 3 — CanonicalNormalizer (adapter dal vecchio output)

**Obiettivo:** convertire `TraderParseResult` (dataclass, base.py) → `CanonicalMessage` senza toccare i profili.

**Attivita:**
1. Creare `src/parser/canonical_v1/normalizer.py`
2. Funzione principale: `normalize(result: TraderParseResult, context: ParserContext) -> CanonicalMessage`
3. Migrare business logic da `canonical_v2.py`:
   - mapping intents → `UpdateOperationType` (SET_STOP / CLOSE / CANCEL_PENDING / MODIFY_ENTRIES / MODIFY_TARGETS)
   - mapping intents → `ReportEventType` (TP_HIT / STOP_HIT / ecc)
   - normalizzazione stop levels → `StopTarget`
   - normalizzazione close → `CloseOperation`
   - targeting → `Targeting` canonico
4. Creare `tests/parser_canonical_v1/test_normalizer.py` con casi per Trader A

**Mapping obbligatorio da fare:**

| Vecchio intent | Vecchio action | Nuovo canonical |
|----------------|----------------|-----------------|
| `U_MOVE_STOP` | `MOVE_STOP` | `UPDATE / SET_STOP PRICE` |
| `U_MOVE_STOP_TO_BE` | `MOVE_STOP` | `UPDATE / SET_STOP ENTRY` |
| `U_CLOSE_FULL` | `CLOSE_POSITION` | `UPDATE / CLOSE full` |
| `U_CLOSE_PARTIAL` | `CLOSE_POSITION` | `UPDATE / CLOSE partial` |
| `U_CANCEL_PENDING_ORDERS` | `CANCEL_PENDING` | `UPDATE / CANCEL_PENDING` |
| `U_ADD_ENTRY` | `ADD_ENTRY` | `UPDATE / MODIFY_ENTRIES ADD` |
| `U_REENTER` | `REENTER_POSITION` | `UPDATE / MODIFY_ENTRIES REENTER` |
| `U_UPDATE_TAKE_PROFITS` | `UPDATE_TAKE_PROFITS` | `UPDATE / MODIFY_TARGETS REPLACE_ALL` |
| `U_TP_HIT` | `MARK_TP_HIT` | `REPORT / TP_HIT` |
| `U_STOP_HIT` | `MARK_STOP_HIT` | `REPORT / STOP_HIT` |
| `U_REPORT_FINAL_RESULT` | `ATTACH_RESULT` | `REPORT / FINAL_RESULT` |
| `NS_CREATE_SIGNAL` | `CREATE_SIGNAL` | `SIGNAL` |

> ✅ **Decisione #3 — RISOLTA:** Intent orfani mappati al tipo più vicino:
> - `U_ACTIVATION` → `REPORT / ENTRY_FILLED`
> - `U_MARK_FILLED` → `REPORT / ENTRY_FILLED`
> - `U_INVALIDATE_SETUP` → `UPDATE / CANCEL_PENDING`
> - `U_REVERSE_SIGNAL` → `UPDATE / CLOSE` only (la parte new signal viene ignorata, warning esplicito)
> - `U_RISK_NOTE` → `INFO`

> ✅ **Decisione #4 — RISOLTA:** `U_EXIT_BE` → `REPORT / BREAKEVEN_EXIT`. È un evento passato (informativo), non un ordine operativo.

**Exit criteria:** dato un `TraderParseResult` di Trader A costruito a mano, il normalizer produce `CanonicalMessage` valido.

---

### FASE 4 — Shadow mode (doppia emissione) — IN CORSO

**Obiettivo:** far girare il normalizer in parallelo senza cambiare nulla al flusso attuale.

**Attivita:**
1. In registry o router, aggiungere chiamata opzionale al normalizer dopo il parse
2. Il risultato canonico viene loggato / salvato
3. Il vecchio flusso e **invariato** — shadow e read-only

> ✅ **Decisione #5 — RISOLTA:** Tabella separata `parse_results_v1`. Richiede migration DB scritta da Claude Code (vedi Decisione #6). Storage layer esteso di conseguenza.

**Exit criteria:** ogni messaggio parsato da Trader A produce anche un `CanonicalMessage` visibile.

---

### FASE 5 — Audit differenziale su dataset reale — COMPLETATA 2026-04-22

**Obiettivo:** misurare gap tra vecchio output e nuovo.

**Attivita:**
1. Usare `parser_test/scripts/replay_parser.py` su dataset Trader A
2. Confrontare per ogni messaggio: vecchio `actions_structured` vs nuovo `CanonicalMessage`
3. Report su:
   - % messaggi che producono `CanonicalMessage` valido
   - % `PARSED` vs `PARTIAL` vs `UNCLASSIFIED`
   - casi di mismatch class (es. vecchio UPDATE → nuovo REPORT)
   - intent non mappati
4. Backlog correzioni al normalizer

**Stato base code 2026-04-22:** disponibile tooling di audit su `parser_test` DB che ricostruisce `TraderParseResult` da `parse_results.parse_result_normalized_json`, esegue il normalizer v1 e genera artefatti `summary.json` + `rows.csv` con mismatch class e intent non mappati.

**Esecuzione reale 2026-04-22:** audit eseguito sul DB corretto multi-trader `db/parser_test__chat_-1003171748254.sqlite3`, lavorando su clone di test e separando i risultati per `trader_a`, `trader_b`, `trader_c`, `trader_d`, `trader_3`. Artefatti salvati in `.test_tmp/phase5_multi/`.

**Exit criteria:** report con mismatch classificati, nessun crash del normalizer su dataset reale.

---

### FASE 6 — Stabilizzazione normalizer — COMPLETATA 2026-04-22

**Obiettivo:** portare il normalizer a copertura sufficiente su Trader A.

**Attivita:**
1. Correggere mismatch trovati in Fase 5
2. Aggiungere test regression per ogni caso corretto
3. Target: >= 90% dei messaggi Trader A producono `PARSED` o `PARTIAL` corretto

**Stato base code 2026-04-22:**
- introdotto contratto parser-side minimo runtime `TraderEventEnvelopeV1` in `src/parser/event_envelope_v1.py`
- introdotto adapter centrale `TraderParseResult -> TraderEventEnvelopeV1` in `src/parser/adapters/legacy_to_event_envelope_v1.py`
- il normalizer v1 usa gia l'adapter centrale invece di ricostruire direttamente i payload business dal legacy grezzo
- corretto `parser_test/scripts/replay_parser.py` per persistere nel JSON replayato anche:
  - `reported_results`
  - `primary_intent`
  - `target_scope`
  - `linking`
  - `diagnostics`
- aggiunti test mirati adapter/normalizer per:
  - precedenza `entry_plan_entries > entries > entry`
  - `U_MOVE_STOP_TO_BE -> SET_STOP`
  - `UPDATE + REPORT`
  - alias legacy `U_UPDATE_STOP`
  - alias legacy `U_REMOVE_PENDING_ENTRY`

**Verifica reale gia eseguita 2026-04-22 su clone DB `db/parser_test__chat_-1003171748254.sqlite3`:**
- replay completo `trader_c` su clone di test
- audit canonical v1 dopo replay
- risultato:
  - `total rows: 398`
  - `canonical valid rows: 398`
  - `normalizer error rows: 0`
  - restano mismatch `UPDATE -> REPORT`, ma senza errori di normalizzazione

**Chiusura formale 2026-04-22 — audit Trader A eseguito su `db/parser_test__chat_-1003171748254.sqlite3`:**
- `total rows: 836`
- `canonical valid rows: 836` (100%)
- `normalizer error rows: 0`
- `PARSED: 804`, `PARTIAL: 3`, `UNCLASSIFIED: 29` → **807/836 = 96.5%** ✅ (target >=90%)
- `unmapped_intent_counts: {}` — nessun intent non mappato
- `class_mismatch_rows: 244`:
  - `UPDATE->REPORT: 226` — riallineamento semantico atteso (TP_HIT/STOP_HIT/ENTRY_FILLED erano classificati come UPDATE nel legacy)
  - `INFO_ONLY->REPORT: 16` — idem (FINAL_RESULT/STOP_HIT veicolati come INFO_ONLY nel legacy)
  - `INFO_ONLY->UPDATE: 2` — intent UPDATE in messaggi classificati INFO_ONLY nel legacy
- `29 UNCLASSIFIED`: tutti già UNCLASSIFIED nel legacy, zero intents — il normalizer li gestisce correttamente come INFO

**Fix di regressione identificati durante chiusura:**
- `U_MOVE_STOP` senza `new_stop_level`: l'adapter non generava warning — aggiunto `"U_MOVE_STOP: new_stop_level missing or unresolvable"` in `_build_update_payload`
- `U_REVERSE_SIGNAL`: non gestito nell'adapter — aggiunta mappatura a `CLOSE FULL` + warning `"U_REVERSE_SIGNAL: new signal component ignored; mapped to CLOSE only"` in `_intent_to_update_operation`
- Entrambi i fix in `src/parser/adapters/legacy_to_event_envelope_v1.py`

**Exit criteria:** ✅ suite test stabile — `104/104 PASSED`, nessuna regressione.

---

### FASE 7 — Adattare il router al modello v1 — COMPLETATA 2026-04-22

**Obiettivo:** il router legge e persiste `CanonicalMessage` come payload primario.

**Attivita:**
1. Aggiornare `src/telegram/router.py` per chiamare il normalizer dopo il parse
2. Persistere `CanonicalMessage` come JSON nella colonna principale
3. Mantenere colonne sintetiche legacy come cache (non source of truth)

> ✅ **Decisione #6 — RISOLTA:** Claude Code scrive i file SQL di migration seguendo il pattern esistente in `db/migrations/`. L'utente rivede e applica manualmente. CLAUDE.md va aggiornato per autorizzare esplicitamente le migration legate a canonical_v1.

**Esecuzione reale 2026-04-22:**

- **Migration non necessaria**: `parse_results_v1` (migration 020) ha già tutto. "Colonna principale" = `parse_results_v1.canonical_json`.
- **Rimosso flag `_shadow_enabled`**: normalizzazione v1 always-on quando `_parse_results_v1 is not None`.
- **Aggiunto `parse_results_v1_store`** come parametro costruttore di `MessageRouter`.
- **`main.py`**: v1 store sempre collegato via costruttore; `_configure_shadow_mode` mantenuta per backward compat ma non chiamata in `_async_main`.
- **`enable_shadow_normalizer()` / `disable_shadow_normalizer()`** mantenuti per API compat — settano/azzerano `_parse_results_v1`.
- **Test**: 9 nuovi in `src/telegram/tests/test_router_canonical_v1.py` PASSED; 16 test shadow/main preesistenti PASSED (zero regressioni).

**Exit criteria:** ✅ Canonical v1 persisted per ogni messaggio parsato in produzione. Layer downstream (Fase 8) ancora su `TraderParseResult` — verrà migrato profilo per profilo.

---

### FASE 8 — Migrazione profili trader (uno alla volta)

**Obiettivo:** ogni profilo emette direttamente `CanonicalMessage` senza passare per il normalizer.

**Ordine (dal piu semplice al piu complesso):**
1. `trader_3` (497 righe) ✅ COMPLETATO 2026-04-22
2. `trader_b` ✅ COMPLETATO 2026-04-22
3. `trader_c` ✅ COMPLETATO 2026-04-23
4. `trader_d` ✅ COMPLETATO 2026-04-23
5. `trader_a` (1879 righe — il piu ricco di eccezioni, ultimo) ✅ COMPLETATO 2026-04-23

**Per ogni trader:**
1. Leggere `parsing_rules.json` del profilo
2. Adattare `profile.py` per produrre `CanonicalMessage` direttamente
3. Confrontare con l'output del normalizer (shadow diff)
4. Passare in modalita nativa quando diff < soglia accettabile
5. Aggiornare i test del profilo per testare `CanonicalMessage`

**Architettura adottata 2026-04-22:**
- Aggiunto `TraderProfileParserV1` Protocol in `src/parser/trader_profiles/base.py` con metodo `parse_canonical(text, context) → CanonicalMessage`
- Profili v1-nativi dichiarano `parse_canonical()` sulla classe (non su istanza)
- Il router rileva i profili v1-nativi con `callable(getattr(type(parser), 'parse_canonical', None))` (evita MagicMock false-positive nei test)
- Profili non ancora migrati continuano con `_shadow_normalize` (normalizer legacy)
- `parse_message()` rimane invariato per backward compat verso lo storage legacy

**Esecuzione trader_3 2026-04-22:**
- `parse_canonical()` implementato in `Trader3ProfileParser` — 30 nuovi test, tutti PASSED
- Mappature: `NEW_SIGNAL→SIGNAL/RANGE`, `U_TP_HIT→REPORT/TP_HIT`, `U_STOP_HIT→REPORT/STOP_HIT`, `U_CLOSE_FULL→UPDATE/CLOSE FULL`, `U_REENTER→UPDATE/PARTIAL` (no entry prices), bare loss report → `REPORT/reported_result`
- Helper functions `_build_t3_targeting`, `_build_t3_signal_payload`, `_build_t3_update_ops`, `_build_t3_report_payload` — module-level in `profile.py`
- Targeting usa `EXPLICIT_ID` per `signal_id` (corretto rispetto al normalizer legacy che lo ignorava)

**Esecuzione trader_c 2026-04-23:**
- `parse_canonical()` implementato in `TraderCProfileParser`, con `parse_message()` legacy invariato
- Mappature native implementate:
  - `NEW_SIGNAL -> SIGNAL`
  - `U_MOVE_STOP_TO_BE/U_MOVE_STOP/U_UPDATE_STOP -> UPDATE/SET_STOP`
  - `U_CLOSE_FULL/U_CLOSE_PARTIAL -> UPDATE/CLOSE`
  - `U_CANCEL_PENDING_ORDERS/U_REMOVE_PENDING_ENTRY -> UPDATE/CANCEL_PENDING`
  - `U_UPDATE_TAKE_PROFITS -> UPDATE/MODIFY_TARGETS`
  - `U_ACTIVATION/U_TP_HIT/U_STOP_HIT/U_EXIT_BE -> REPORT`
- Aggiunti helper module-level in `profile.py`:
  `_build_tc_targeting`, `_build_tc_signal_payload`, `_build_tc_update_ops`,
  `_build_tc_report_events`, `_build_tc_reported_result`
- Nuova suite: `src/parser/trader_profiles/trader_c/tests/test_canonical_output.py`
- Verifica eseguita: `99 passed` su `src/parser/trader_profiles/trader_c/tests`

**Esecuzione trader_a 2026-04-23:**
- `parse_canonical()` implementato in `TraderAProfileParser`, mantenendo `parse_message()` legacy invariato
- Mappature native implementate:
  - `NEW_SIGNAL/SETUP_INCOMPLETE -> SIGNAL` (con `entry_plan_entries` -> `EntryLeg` canonici)
  - `U_MOVE_STOP_TO_BE/U_MOVE_STOP -> UPDATE/SET_STOP`
  - `U_CLOSE_FULL/U_CLOSE_PARTIAL -> UPDATE/CLOSE`
  - `U_CANCEL_PENDING_ORDERS/U_INVALIDATE_SETUP -> UPDATE/CANCEL_PENDING`
  - `U_UPDATE_TAKE_PROFITS -> UPDATE/MODIFY_TARGETS`
  - `U_TP_HIT/U_STOP_HIT/U_MARK_FILLED/U_EXIT_BE/U_REPORT_FINAL_RESULT -> REPORT`
- Aggiunti helper module-level in `profile.py`:
  `_build_ta_targeting`, `_build_ta_signal_payload`, `_build_ta_update_ops`,
  `_build_ta_report_payload`, `_build_ta_report_events`, `_build_ta_reported_result`
- Nuova suite: `src/parser/trader_profiles/trader_a/tests/test_canonical_output.py`
- Verifica eseguita:
  - `10 passed` su `test_canonical_output.py`
  - `118 passed` su tutta la suite `src/parser/trader_profiles/trader_a/tests`

**Exit criteria:** ✅ tutti i profili emettono `CanonicalMessage` nativamente.

---

### FASE 9 — Deprecazione legacy

**Sequenza:**
1. Rimuovere `canonical_v2.py` (sostituito dal normalizer)
2. Rimuovere `src/parser/models/canonical.py` (legacy Pydantic)
3. Rimuovere `models/new_signal.py`, `update.py`, `operational.py`
4. Rimuovere il normalizer se i profili emettono direttamente
5. Aggiornare `CLAUDE.md` con nuova architettura

**Exit criteria:** un solo contratto, un solo path, niente legacy attivo.

---

## Checklist completa

```
FASE 1 — Congelamento contratto  ✅ COMPLETATA 2026-04-22
[x] Creare src/parser/canonical_v1/__init__.py
[x] Spostare canonical_parser_model_v1.py → src/parser/canonical_v1/models.py
[x] Copiare Price + normalize_price() in canonical_v1/models.py, rinominare PriceValue → Price
[x] Verificare che import CanonicalMessage e Price funzionino
[x] Marcare src/parser/models/canonical.py come LEGACY
[x] Marcare models/new_signal.py, update.py, operational.py come LEGACY
[ ] Aggiornare CLAUDE.md per autorizzare Claude Code a scrivere migration DB

FASE 2 — Fixture e test schema  ✅ COMPLETATA 2026-04-22
[x] Creare tests/parser_canonical_v1/
[x] Scrivere fixture per SIGNAL (ONE_SHOT, TWO_STEP, RANGE, LADDER, PARTIAL)
[x] Scrivere fixture per UPDATE (tutti i 5 op types)
[x] Scrivere fixture per REPORT (tutti gli event types)
[x] Scrivere fixture UPDATE+REPORT composito
[x] Scrivere fixture INFO
[x] Scrivere test casi negativi (tutti i vincoli del validator)
[x] Tutti i test passano  — 46/46 PASSED

FASE 3 — CanonicalNormalizer  ✅ COMPLETATA 2026-04-22
[x] Creare src/parser/canonical_v1/normalizer.py
[x] Implementare normalize(result, context) → CanonicalMessage
[x] Mappare tutti gli intent UPDATE → UpdateOperation (vedi tabella)
[x] Normalizer setta close_scope="FULL" quando U_CLOSE_FULL senza scope in entities
[x] Mappare U_ACTIVATION / U_MARK_FILLED → REPORT/ENTRY_FILLED
[x] Mappare U_INVALIDATE_SETUP → UPDATE/CANCEL_PENDING
[x] Mappare U_REVERSE_SIGNAL → UPDATE/CLOSE + warning "new signal ignorato"
[x] Mappare U_RISK_NOTE → INFO
[x] Mappare U_EXIT_BE → REPORT/BREAKEVEN_EXIT
[x] Mappare intent REPORT → ReportEvent (TP_HIT, STOP_HIT, FINAL_RESULT)
[x] Mappare NS_CREATE_SIGNAL → SignalPayload
[x] Mappare targeting (REPLY, TELEGRAM_LINK, GLOBAL) → Targeting canonico
[x] Normalizzare stop levels → StopTarget
[x] Creare tests/parser_canonical_v1/test_normalizer.py
[x] Test con TraderParseResult costruiti a mano per Trader A  — 33/33 PASSED

FASE 4 — Shadow mode  🚧 IN CORSO 2026-04-22
[x] Scrivere migration DB: tabella parse_results_v1
[x] Estendere storage layer per scrivere su parse_results_v1
[x] Aggiungere chiamata normalizer in parallelo al flusso esistente
[x] Verificare che il vecchio flusso sia invariato
[x] Shadow mode attivabile via config flag (`PARSER_V1_SHADOW_MODE=true`)
[x] Aggiungere test dedicati per storage/router/bootstrap shadow mode — `16 passed`
[ ] Verifica end-to-end su listener live con DB reale popolato
[ ] Rendere osservabile il volume shadow in audit/report operativi

FASE 5 — Audit differenziale  ✅ COMPLETATA 2026-04-22
[x] Creare base code audit differenziale su `parser_test` DB
[x] Ricostruire `TraderParseResult` da `parse_results.parse_result_normalized_json`
[x] Eseguire `CanonicalNormalizer` v1 su dataset replayato
[x] Produrre artefatti audit (`summary.json` + `rows.csv`)
[x] Esporre mismatch class (`legacy message_type` → `canonical primary_class`)
[x] Esporre conteggio intent non mappati
[x] Aggiungere CLI dedicato `parser_test/scripts/audit_canonical_v1.py`
[x] Aggiungere test dedicati per audit tool — `2 passed`
[x] Eseguire replay su dataset Trader A
[x] Produrre report gap vecchio/nuovo su dataset reale Trader A
[x] Classificare tutti i mismatch reali
[x] Backlog correzioni normalizer compilato

FASE 6 — Stabilizzazione  ✅ COMPLETATA 2026-04-22
[x] Introdurre `TraderEventEnvelopeV1` come bridge parser-side minimo runtime
[x] Implementare adapter centrale `TraderParseResult -> TraderEventEnvelopeV1`
[x] Far leggere al normalizer v1 il bridge centrale invece del legacy grezzo
[x] Correggere mismatch prioritari emersi su trader_c (`TWO_STEP`, stop/update alias, remove pending entry)
[x] Aggiungere test regression per i fix del bridge/normalizer
[x] Verifica reale su clone DB trader_c: `398/398` validi, `0` normalizer errors
[x] Classificare formalmente i mismatch residui `UPDATE -> REPORT` come riallineamento semantico atteso
[x] Chiudere target esplicito su Trader A
[x] >= 90% messaggi Trader A → PARSED o PARTIAL corretto (`807/836`, audit 2026-04-22)

FASE 7 — Router  ✅ COMPLETATA 2026-04-22
[x] Scrivere migration DB per colonna canonical_v1_json in parse_results (se necessaria) — NON necessaria: parse_results_v1 è sufficiente
[x] Aggiornare router: rimosso _shadow_enabled, parse_results_v1_store come param costruttore
[x] Persistere canonical JSON — sempre scritto in parse_results_v1 quando store collegato
[x] Test integration router + normalizer — 9 test in test_router_canonical_v1.py PASSED

FASE 8 — Migrazione profili (uno alla volta)  ✅ COMPLETATA 2026-04-23
[x] trader_3: adattare profile.py → CanonicalMessage nativo (parse_canonical())
[x] trader_3: aggiornare test (30 nuovi test in test_canonical_output.py — PASSED)
[x] trader_b: adattare profile.py → CanonicalMessage nativo (parse_canonical() + 7 helper functions)
[x] trader_b: aggiornare test (43 nuovi test in test_canonical_output.py — PASSED)
[x] trader_c: adattare profile.py → CanonicalMessage nativo (parse_canonical() + helper canonical)
[x] trader_c: aggiornare test (nuovo test_canonical_output.py — PASSED)
[x] trader_d: adattare profile.py → CanonicalMessage nativo (parse_canonical() + 6 helper functions module-level)
[x] trader_d: aggiornare test (32 nuovi test in test_canonical_output.py — PASSED 2026-04-23)
[x] trader_a: adattare profile.py → CanonicalMessage nativo (parse_canonical() + helper canonical module-level)
[x] trader_a: aggiornare test (nuovo test_canonical_output.py + regressione suite trader_a: 118 passed)

FASE 9 — Deprecazione
[ ] Rimuovere canonical_v2.py
[ ] Rimuovere src/parser/models/canonical.py
[ ] Rimuovere models/new_signal.py, update.py, operational.py
[ ] Aggiornare CLAUDE.md con nuova architettura definitiva
[ ] Verificare zero import rotti
```
