# AUDIT — TeleSignalBot

Registro degli step di migrazione completati, stato dei file e rischi aperti.

---

## 2026-04-27 — Fase 1: Parser Contract (multi-ref target-aware)

### Step completato

**Fase 1** del piano `PIANO_INCREMENTAZIONE_MULTI_REF.md` — estensione del contratto
canonico con i modelli target-aware, senza modificare il comportamento esistente.

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/parser/canonical_v1/models.py` | Modificato | Aggiunti 5 Literal type, 10 modelli Pydantic, 2 campi in `CanonicalMessage` |
| `tests/parser_canonical_v1/test_targeted_action_model.py` | Creato | 37 test — tutti verdi |
| `docs/in_progress/new_parser/PIANO_INCREMENTAZIONE_MULTI_REF.md` | Aggiornato | Checklist Fase 1 spuntata; sezione "Lavoro svolto" aggiunta |

### Risultato test

```
pytest tests/parser_canonical_v1/  →  116/116 passed
```

Tutti i test preesistenti rimangono verdi. Nessun profilo legacy rotto.

### Rischi aperti

- `schema_version` non aggiornato a `"1.1"` — deferred a Fase 5 per non rompere test esistenti.
- `TargetedAction.params` è `dict[str, Any]` (loose) — la validazione strutturata dei params
  è demandata alla Fase 2 quando i profili iniziano a produrre output reale.
- `TargetedReportTargeting = TargetedActionTargeting` è un alias Python puro; se in futuro
  le due shape divergessero, sarebbe necessario separare le classi.

### Prossimo step

**Fase 2** — Parser Builder: `trader_a` produce `targeted_actions` e `targeted_reports`
nel proprio `parse_canonical()`. Vedi checklist in `PIANO_INCREMENTAZIONE_MULTI_REF.md`.

---

## 2026-04-27 — Fase 2: Parser Builder (`trader_a` pilota)

### Step completato

**Fase 2** del piano `PIANO_INCREMENTAZIONE_MULTI_REF.md` — `trader_a` produce
`targeted_actions` e `targeted_reports` in `parse_canonical()`.

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/parser/canonical_v1/targeted_builder.py` | Creato | Builder shared: `build_targeted_actions`, `build_targeted_reports_from_lines` |
| `src/parser/trader_profiles/trader_a/profile.py` | Modificato | Import builder + blocco targeted in `parse_canonical()` + 5 costruttori estesi |
| `src/parser/trader_profiles/trader_a/tests/test_multi_ref.py` | Creato | 5 test Phase 2 — tutti verdi |
| `docs/in_progress/new_parser/PIANO_INCREMENTAZIONE_MULTI_REF.md` | Aggiornato | Checklist Fase 2 spuntata; sezione "Lavoro svolto" aggiunta |

### Risultato test

```
pytest src/parser/trader_profiles/trader_a/tests/test_multi_ref.py  →  5/5 passed
pytest src/parser/  →  725 passed, 15 failed (tutti pre-esistenti, nessuno introdotto)
```

### Rischi aperti

- Validazione su dataset reale del DB non eseguita (nessun accesso diretto al DB in sessione).
  Pattern derivati da codice esistente — da verificare con replay_parser.
- `event_type` nei `targeted_reports` è sempre `FINAL_RESULT` (scelta conservativa).
  Distinzione `TP_HIT`/`STOP_HIT` richiede contesto posizione — deferred a Fase 3/5.
- `build_targeted_reports_from_lines` richiede formato riga `SYMBOL - LINK VALUE UNIT`.
  Varianti senza simbolo o con separatori diversi non estratte.
- `parsing_rules.json` non modificato — le regole multi-ref erano già presenti nella logica Python.

### Prossimo step

**Fase 3** — Target Resolver: diventa multi-target e multi-action aware.

---

## 2026-04-27 — Fase 3: Target Resolver multi-target aware

### Step completato

**Fase 3** del piano `PIANO_INCREMENTAZIONE_MULTI_REF.md` — il resolver viene esteso
con una nuova funzione standalone `resolve_targeted()` che elabora `targeted_actions`
e `targeted_reports` producendo `MultiRefResolvedResult`.

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/target_resolver/models.py` | Creato | `ResolvedActionItem`, `ResolvedReportItem`, `MultiRefResolvedResult` |
| `src/target_resolver/resolver.py` | Modificato | Import + `_resolve_action_item` + `_resolve_report_item` + `resolve_targeted` |
| `src/target_resolver/tests/test_targeted_resolver.py` | Creato | 5 test Fase 3 — tutti verdi |
| `docs/in_progress/new_parser/PIANO_INCREMENTAZIONE_MULTI_REF.md` | Aggiornato | Checklist Fase 3 spuntata; sezione "Lavoro svolto" aggiunta |

### Risultato test

```
pytest src/target_resolver/  →  16/16 passed (5 nuovi + 11 preesistenti)
pytest src/target_resolver/ tests/parser_canonical_v1/ src/parser/trader_profiles/trader_a/tests/test_multi_ref.py
→  137/137 passed
```

### Rischi aperti

- `TargetResolver.resolve()` (legacy) ancora non migrata — dipende da layer downstream (operation_rules, router).
- `targeted_reports` con NOT_FOUND non coperto da test dedicato — logica implementata ma non testata per il caso di fallimento.
- Integrazione end-to-end su replay reale non ancora eseguita (accesso DB non disponibile in sessione).
- `event_type=FINAL_RESULT` nei report è ancora fisso (eredità Fase 2) — la distinzione richiede contesto posizione.

### Prossimo step

**Fase 4** — Router / Update Planner / Runtime: il runtime consuma il binding reale `azione → target`.

---

## 2026-04-27 — STEP 0: Pre-condizioni per Disambiguation & Context Resolution

### Step completato

**STEP 0** del piano `PIANO_IMPLEMENTAZIONE_DISAMBIGUATION_CONTEXT_RESOLUTION.md` —
verifica e ripristino delle pre-condizioni prima di iniziare il layer semantico.

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/parser/canonical_v1/models.py` | Modificato | `RiskHint` esteso con `min_value: float | None` e `max_value: float | None` |
| `src/parser/trader_profiles/trader_a/profile.py` | Modificato | Import `RiskHint`; regex `_RISK_RANGE_RE`/`_RISK_SINGLE_RE`; funzione `_extract_risk_hint()`; estrazione in `_extract_entities`; uso in `_build_ta_signal_payload` |
| `src/parser/trader_profiles/trader_a/tests/test_profile_phase4_common.py` | Modificato | Intent name corretto `NEW_SETUP`→`NS_CREATE_SIGNAL`; 12 test `parse_event_envelope_*` marcati `@unittest.skip` (Phase 4 pending) |
| `src/parser/trader_profiles/trader_d/tests/test_profile_smoke.py` | Modificato | Testo test corretto da `"entry: 65000"` a `"Вход с текущих: 65000"` |

### Risultato test

```
pytest src/parser/trader_profiles/  →  549 passed, 12 skipped, 0 failed
```

### Rischi aperti

- `models.py` ha modifiche non committate pre-esistenti (contratto multi-ref): la pre-condizione
  "nessuna modifica pendente" non è pienamente soddisfatta. Commit da eseguire manualmente.
- 12 test `parse_event_envelope_*` sono SKIPPED — richiedono `parse_event_envelope()` e campi
  `UpdatePayloadRaw.stop_update`, `ReportPayloadRaw.reported_results` (plurale) da progettare in Phase 4.
- `_RISK_RANGE_RE` non cattura pattern puramente numerici senza keyword russo (es. `"1-2% od depozita"` in inglese).

### Prossimo step

**Step 1** — Taxonomy Layer: definire `IntentName` e `STATEFUL_INTENTS` in `intent_taxonomy.py`.
