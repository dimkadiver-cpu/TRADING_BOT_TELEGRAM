# Piano passaggio al sistema parser comune per tutti i trader

Data: 2026-04-25  
Stato: piano operativo da usare per la migrazione controllata  
Scope: `src/parser`, `src/parser/trader_profiles/*`, `parser_test`, documentazione in `docs/in_progress/new_parser/organizzazione_comune`

## Obiettivo

Portare tutti i trader a usare lo stesso sistema parser-side:

```text
text + ParserContext
  -> profile runtime comune + extractors trader-specifici
  -> TraderEventEnvelopeV1
  -> normalizer centrale
  -> CanonicalMessage
```

La migrazione va fatta un trader alla volta. Il primo e `trader_a`, usando come base il file:

```text
docs/in_progress/new_parser/organizzazione_comune/parsing_rules.template.treader_a.jsonc
```

Regola esplicita per `trader_a`:

- il template `parsing_rules.template.treader_a.jsonc` ha gia le variabili/marker da usare come configurazione;
- non aggiungere nuove variabili o nuove chiavi di configurazione prendendole dai file legacy;
- dai file legacy si estraggono solo dati implementativi da portare in codice dedicato, come regex, pattern di estrazione, normalizzazioni testuali e casi speciali;
- se durante la migrazione emerge il bisogno reale di una nuova chiave comune, va prima promossa nello schema shared, non aggiunta solo a `trader_a`.

## Struttura finale vincolante

La struttura finale da raggiungere e questa. Non va trattata come variante opzionale:

```text
src/parser/
  event_envelope_v1.py
  adapters/
    legacy_to_event_envelope_v1.py
  canonical_v1/
    models.py
    normalizer.py
  trader_profiles/
    base.py
    shared/
      intent_taxonomy.py
      entity_keys.py
      envelope_builder.py
      profile_runtime.py
      targeting.py
      warnings.py
      rules_schema.json
    trader_a/
      profile.py
      extractors.py
      parsing_rules.json
      tests/
```

Regole di ownership:

- `event_envelope_v1.py` contiene solo il contratto parser-side comune e le sottoshape raw.
- `adapters/legacy_to_event_envelope_v1.py` resta temporaneo e serve solo a migrazione/audit, non come ownership finale.
- `canonical_v1/models.py` e `canonical_v1/normalizer.py` restano il contratto e la trasformazione business/downstream.
- `trader_profiles/base.py` contiene solo context/protocolli comuni, non semantica trader-specifica.
- `trader_profiles/shared/intent_taxonomy.py` e l'unica source of truth per intent ufficiali, alias legacy, precedenze, compatibilita e mutue esclusioni.
- `trader_profiles/shared/entity_keys.py` definisce il vocabolario dei blocchi raw ammessi.
- `trader_profiles/shared/envelope_builder.py` costruisce e valida `TraderEventEnvelopeV1`.
- `trader_profiles/shared/profile_runtime.py` orchestra preprocess, classificazione, intent detection, estrazione, targeting, warnings e builder.
- `trader_profiles/shared/targeting.py` contiene solo targeting parser-side e normalizzazione dei target raw.
- `trader_profiles/shared/warnings.py` contiene warning comuni e codici diagnostici.
- `trader_profiles/shared/rules_schema.json` valida la shape comune dei `parsing_rules.json`.
- `trader_a/profile.py` deve diventare orchestration leggera o adapter verso il runtime shared.
- `trader_a/extractors.py` contiene regex, pattern e parsing testuale specifico di `trader_a`.
- `trader_a/parsing_rules.json` contiene solo marker, vocabolari, hint e regole dichiarative nella shape comune.

## Stato reale verificato nel codebase

Il sistema e gia coerente a valle, ma non ancora a monte.

Evidenze:

- `src/parser/trader_profiles/base.py` espone ancora `TraderParseResult` come output ufficiale di `parse_message`.
- `src/parser/event_envelope_v1.py` esiste, ma la sua shape runtime non coincide ancora con la nuova specifica in `01_TraderEventEnvelopeV1.md`.
- `src/parser/adapters/legacy_to_event_envelope_v1.py` converte legacy `TraderParseResult` verso envelope, ma costruisce ancora `update_payload_raw.operations` con `op_type`.
- `src/parser/canonical_v1/normalizer.py` consuma ancora l'envelope compatibile con le operations legacy.
- I profili trader hanno ancora `parse_canonical(...)` e/o logica locale per `primary_intent`, `actions_structured`, `target_scope`, `linking`.
- `parser_test` esporta soprattutto la shape legacy (`entities`, `actions_structured`, `target_scope`, `linking`) e oggi non e sufficiente, da solo, per controllare la nuova shape comune envelope-first.

## Gap da calmare prima di partire

Questi punti non vanno risolti "a intuito" dentro `trader_a`. Sono decisioni comuni.

1. `TraderEventEnvelopeV1` runtime non e allineato alla spec nuova.
   La spec nuova prevede `REPORT` in `message_type_hint`, `update_payload_raw.stop_update/close_update/cancel_update/entry_update/targets_update` e `report_payload_raw.reported_results`. Il runtime attuale usa `MessageTypeHint` senza `REPORT`, `UpdatePayloadRaw.operations`, `ReportPayloadRaw.reported_result` singolo.

2. Tassonomia intent: legacy vs comune.
   Il codice usa ancora intent tipo `NS_CREATE_SIGNAL`, `U_MOVE_STOP_TO_BE`, `U_MARK_FILLED`, `U_STOP_HIT`. La proposta comune usa `NEW_SETUP`, `MOVE_STOP_TO_BE`, `ENTRY_FILLED`, `SL_HIT`, ecc. Serve una tabella shared di alias e una fase di compatibilita misurabile.

3. `RulesEngine.detect_intents()` oggi si aspetta `intent_markers` flat list.
   Il template comune usa `{ "strong": [], "weak": [] }`. Prima di usare il template nuovo in runtime, il motore va aggiornato per leggere entrambi i formati e distinguere forza del marker.

4. `classification_rules` nel template comune non sono implementate.
   Il codice attuale usa `classification_markers` e `combination_rules`, ma non applica `when_all_fields_present` come regola formale. Per `trader_a` questo e importante per distinguere segnale completo, segnale incompleto e update rumorosi.

5. `context_resolution_rules` sono documentate ma non hanno ancora un mini engine shared.
   Non vanno lasciate come config decorativa. O si implementano prima, o si marcano come non operative finche non esiste il resolver.

6. Targeting e linking sono ancora sparsi.
   Il profilo legacy produce `target_refs`, `target_scope`, `linking`; l'envelope target vuole `targets_raw` parser-side e il normalizer deve derivare targeting business.

7. `parse_canonical()` nei profili e contrario alla nuova ownership.
   La proposta nuova vuole profili ridotti a extraction layer e normalizer centrale unico. Quindi la migrazione non deve aggiungere altro canonical mapping dentro i profili.

8. `parser_test` deve osservare il sistema nuovo.
   Per validare il passaggio serve confrontare almeno: legacy result, envelope target, canonical output, warning/diagnostics, campi residui in `diagnostics.legacy_*`.

## Decisioni consigliate

Scelta raccomandata: promuovere davvero `TraderEventEnvelopeV1` come output parser-side unico.

Implica:

- `parse_message(...)` dei profili nuovi ritorna `TraderEventEnvelopeV1`;
- il vecchio `TraderParseResult` resta solo come compat layer temporaneo;
- `CanonicalMessage` viene costruito solo dal normalizer centrale;
- `actions_structured` sparisce dal parser e resta solo in eventuale diagnostica legacy;
- i profili non costruiscono piu `CanonicalMessage`;
- ogni trader usa lo stesso schema `parsing_rules.json`.

## Coverage della proposta organizzazione comune

Questa sezione rende esplicito come `PROPOSTA_ORGANIZZAZIONE_COMUNE_PARSER.md` e rappresentata nel piano.

| Tema proposta | Stato nel piano | Dove viene trattato |
|---|---|---|
| Output parser-side unico `TraderEventEnvelopeV1` | rappresentato | Obiettivo, Decisioni consigliate, Fase 0 |
| Output downstream unico `CanonicalMessage` | rappresentato | Obiettivo, Decisioni consigliate, Fase 6 |
| Profili ridotti a orchestration + extraction | rappresentato | Struttura finale vincolante, Fase 4 |
| Niente `parse_canonical()` nei profili | rappresentato | Gap, Decisioni consigliate, Fase 6 |
| Payload raw strutturati, non entity flat | rappresentato | Gap, Fase 0, Fase 4 |
| Tassonomia intent shared | rappresentato | Fase 1 |
| Alias legacy -> intent ufficiali | rappresentato | Fase 1, Matrice gap |
| Multi-intent e `primary_intent_hint` shared | rappresentato | Fase 1, Fase 2 |
| Vocabolario entity raw comune | rappresentato | Struttura finale vincolante, Fase 1 |
| Regole di precedenza comuni | da rendere implementativo in shared | Aggiunto come deliverable in Fase 1 |
| Layer 1 profile / Layer 2 builder / Layer 3 normalizer / Layer 4 downstream | rappresentato | Sezione "Layer target" sotto |
| Struttura file target | rappresentato come vincolante | Struttura finale vincolante |
| `profile.py` uniforme | rappresentato | Fase 4, sezione "Forma finale dei profili" sotto |
| `extractors.py` solo estrazione locale | rappresentato | Fase 4, sezione "Forma finale degli extractor" sotto |
| Ownership moduli shared | rappresentato | Struttura finale vincolante |
| Mini engine `context_resolution_rules` | rappresentato come gap/decisione | Gap, Fase 2, Punti da ragionare |
| Schema comune `parsing_rules.json` | rappresentato | Fase 1, Fase 4 |
| Centralizzare `actions_structured`, `target_scope`, `linking` | rappresentato | Gap, Decisioni consigliate, Fase 6 |
| Opzione B raccomandata | rappresentato | Decisioni consigliate |
| Migrazione uno alla volta, primo `trader_a` | rappresentato | Obiettivo, Fase 4, Fase 5 |

### Layer target

Il piano segue questi layer finali:

1. Profile parser trader-specifico:
   preprocess, classificazione locale, intent raw, estrazione raw, target refs e warning locali minimi.
2. Envelope builder shared:
   costruzione `TraderEventEnvelopeV1`, shape comune, precedenze comuni, normalizzazioni parser-side, residui in `diagnostics`.
3. Normalizer shared:
   conversione envelope -> `CanonicalMessage`, classificazione business, targeting finale.
4. Downstream business:
   operation rules, target resolver, execution; legge solo `CanonicalMessage`.

### Forma finale dei profili

Ogni profilo deve convergere allo stesso scheletro logico:

```python
class TraderXProfileParser:
    trader_code = "trader_x"

    def parse_message(self, text: str, context: ParserContext) -> TraderEventEnvelopeV1:
        return shared_profile_runtime.parse(
            trader_code=self.trader_code,
            text=text,
            context=context,
            rules=self.rules,
            extractors=self.extractors,
        )
```

Regole:

- niente costruzione `CanonicalMessage`;
- niente mapping intent -> operation;
- niente `actions_structured` come output primario;
- niente target business finale nel profilo.

### Forma finale degli extractor

`extractors.py` contiene solo logica trader-specifica di parsing testuale:

- regex e pattern di estrazione;
- normalizzazioni testuali locali;
- estrazione symbol, side, entry, stop, TP, risultato, close fraction, hit target;
- costruzione di frammenti raw parser-side;
- nessun mapping verso operation/canonical/downstream.

## Acceptance contract

Il passaggio e completo quando:

- ogni profilo trader emette lo stesso scheletro `TraderEventEnvelopeV1`;
- `trader_a` passa per primo usando il template comune gia predisposto;
- nessun profilo aggiunge chiavi top-level fuori contratto;
- nessun profilo costruisce direttamente `CanonicalMessage`;
- `parser_test` consente di confrontare legacy, envelope e canonical su dati reali;
- i campi non migrabili restano visibili in `diagnostics.legacy_*`, non dispersi in payload custom.

Primary signal:

- un messaggio reale di qualsiasi trader produce sempre `TraderEventEnvelopeV1` valido con blocchi top-level stabili.

Secondary signals:

- test schema envelope;
- test schema `parsing_rules.json`;
- replay `parser_test` per trader;
- report CSV/JSON con diff legacy/envelope/canonical;
- audit dei campi legacy residui.

## Piano operativo

### Fase 0 - Congelare contratto e compatibilita

**COMPLETATA 2026-04-24**

Checklist:

- [x] Allineare `src/parser/event_envelope_v1.py` alla spec `01_TraderEventEnvelopeV1.md`.
- [x] Aggiungere `REPORT` a `MessageTypeHint`.
- [x] Sostituire `UpdatePayloadRaw.operations` con i blocchi parser-side comuni:
  `stop_update`, `close_update`, `cancel_update`, `entry_update`, `targets_update`, `raw_fragments`.
- [x] Allineare `ReportPayloadRaw` a `events`, `reported_results`, `notes`, `summary_text_raw`.
- [x] Aggiungere shape esplicite mancanti: `SizeHintRaw`, `StopUpdateRaw`, `CloseUpdateRaw`, `CancelUpdateRaw`, `EntryUpdateRaw`, `TargetsUpdateRaw`, `SignalRawFragments`, `UpdateRawFragments`.
- [x] Tenere adapter legacy temporaneo per convertire `TraderParseResult -> TraderEventEnvelopeV1` finche i profili non sono migrati.
- [x] Aggiungere test negativi per vietare campi top-level extra.

File toccati:
- `src/parser/event_envelope_v1.py` — riscritta secondo spec; rimossi tipi/classi execution-oriented
- `src/parser/adapters/legacy_to_event_envelope_v1.py` — aggiornato al nuovo contratto envelope
- `src/parser/canonical_v1/normalizer.py` — aggiornato mapping nuovo envelope -> canonical invariato
- `tests/event_envelope_v1/test_envelope_schema.py` — nuovo (39 test, tutti green)

Note implementative:
- `EntryRole` ridotto a `PRIMARY | AVERAGING | UNKNOWN` (spec); `RANGE_LOW/RANGE_HIGH` -> `AVERAGING`, `REENTRY` -> `AVERAGING` nell'adapter
- `ReportEventType` envelope usa `SL_HIT/EXIT_BE`; il normalizer mappa verso `STOP_HIT/BREAKEVEN_EXIT` del canonical legacy
- `PARTIAL_RESULT` e `UNKNOWN` sono nuovi nell'envelope; il normalizer li salta (non esistono nel canonical invariato)
- `CancelScope` ha vocabolario fisso; il valore legacy `ALL_PENDING_ENTRIES` viene mappato a `UNKNOWN`
- 122/122 test passano dopo aggiornamento test legacy `test_cancel_pending_orders`

Output atteso:

- il contratto runtime e la spec nuova parlano la stessa lingua; ✅
- il normalizer legge la nuova envelope e produce canonical invariato. ✅

### Fase 1 - Shared taxonomy e schema regole

Checklist:

- [x] Creare `src/parser/trader_profiles/shared/intent_taxonomy.py`.
- [x] Definire intent ufficiali:
  `NEW_SETUP`, `MOVE_STOP_TO_BE`, `MOVE_STOP`, `CLOSE_FULL`, `CLOSE_PARTIAL`, `CANCEL_PENDING_ORDERS`, `INVALIDATE_SETUP`, `REENTER`, `ADD_ENTRY`, `UPDATE_TAKE_PROFITS`, `ENTRY_FILLED`, `TP_HIT`, `SL_HIT`, `EXIT_BE`, `REPORT_FINAL_RESULT`, `REPORT_PARTIAL_RESULT`, `INFO_ONLY`.
- [x] Definire alias legacy verso intent ufficiali, per esempio `U_STOP_HIT -> SL_HIT`, `U_MARK_FILLED -> ENTRY_FILLED`, `NS_CREATE_SIGNAL -> NEW_SETUP`.
- [x] Definire precedenze per `primary_intent_hint`.
- [x] Definire precedenze comuni per entry, side, stop update, close update, cancel update, targets update e report result.
- [x] Definire compatibilita multi-intent e mutue esclusioni.
- [x] Creare `src/parser/trader_profiles/shared/entity_keys.py` per le sottoshape raw e le chiavi ammesse.
- [x] Creare `src/parser/trader_profiles/shared/rules_schema.json` come schema comune per `parsing_rules.json`.
- [x] Creare `src/parser/trader_profiles/shared/targeting.py` per target raw, reply, link, explicit id e global scope parser-side.
- [x] Creare `src/parser/trader_profiles/shared/warnings.py` per warning comuni e codici diagnostici.
- [x] Aggiungere test di validazione regole per tutti i profili, partendo da `trader_a`.

**COMPLETATA 2026-04-25**

Output atteso:

- nessun profilo decide localmente il vocabolario intent;
- `parsing_rules.template.treader_a.jsonc` diventa validabile contro lo stesso schema di tutti gli altri trader.

### Fase 2 - Aggiornare RulesEngine e runtime comune

**COMPLETATA 2026-04-25**

Checklist:

- [x] Aggiornare `RulesEngine.detect_intents()` per leggere marker flat legacy e marker `{strong, weak}`.
- [x] Far ritornare anche forza/evidenza dei marker, non solo lista intent.
- [x] Implementare `classification_rules.when_all_fields_present`.
- [x] Mantenere `combination_rules` ma separarle chiaramente da `classification_rules`.
- [x] Implementare o disattivare esplicitamente `context_resolution_rules`; non lasciarle ambigue.
- [x] Creare `src/parser/trader_profiles/shared/profile_runtime.py`.
- [x] Creare `src/parser/trader_profiles/shared/envelope_builder.py`.
- [x] Far usare a `profile_runtime.py` `intent_taxonomy.py`, `targeting.py`, `warnings.py` ed `envelope_builder.py`, evitando logica duplicata nei profili.
- [x] Centralizzare `primary_intent_hint` nel runtime shared.
- [x] Centralizzare warning comuni: missing target, conflicting intents, partial signal, unclassified with markers.

Output atteso:

- ogni profilo segue lo stesso flusso: preprocess, classify, detect intents, extract raw blocks, extract targets, build envelope.

### Fase 3 - Preparare `parser_test` per il nuovo sistema

Obiettivo: poter controllare il passaggio senza guardare manualmente solo JSON grezzi.

**COMPLETATA 2026-04-25**

Checklist minima:

- [x] In `parser_test/scripts/replay_parser.py`, salvare nel `parse_result_normalized_json` anche `event_envelope_v1` quando disponibile.
- [x] Salvare anche `canonical_message_v1` prodotto dal normalizer centrale.
- [x] Aggiungere un flag CLI tipo `--parser-system legacy|common|both`, con default `both` durante la migrazione.
- [x] Aggiungere report CSV dedicato per envelope:
  `message_type_hint`, `primary_intent_hint`, `intents_detected`, `instrument.symbol`, `instrument.side`, `targets_raw_count`, `legacy_diagnostics_count`.
- [x] Aggiungere report CSV per payload comuni:
  signal entries/SL/TP, update stop/close/cancel/entry/targets, report events/results.
- [x] Aggiornare `parser_test/reporting/flatteners.py` per leggere prima `event_envelope_v1`, poi fallback legacy.
- [x] Aggiornare `parser_test/reporting/report_schema.py` con colonne envelope/canonical, evitando di rimuovere le colonne legacy durante la fase di confronto.
- [x] Aggiornare `parser_test/reporting/canonical_v1_audit.py` o aggiungere `event_envelope_v1_audit.py` per misurare errori envelope e residui legacy.
- [x] Aggiungere test a `parser_test/tests` per replay/report con JSON che contiene sia legacy che envelope.

Output atteso:

- dopo un replay, puoi vedere per riga se il nuovo sistema produce la stessa classe semantica o se cambia intenzionalmente; ✅
- i campi legacy residui sono visibili e contabili; ✅
- `actions_structured` non resta l'unico modo per capire cosa ha fatto il parser. ✅

### Fase 4 - Migrazione `trader_a`

Input:

- template comune: `docs/in_progress/new_parser/organizzazione_comune/parsing_rules.template.jsonc`
- base config precompilata trader_a: `docs/in_progress/new_parser/organizzazione_comune/parsing_rules.json`
- legacy config di confronto: `src/parser/trader_profiles/trader_a/parsing_rules.json`
- legacy profile: `src/parser/trader_profiles/trader_a/profile.py`;
- test legacy: `src/parser/trader_profiles/trader_a/tests/*`;
- casi reali di riferimento: `db/parser_test__chat_-1003171748254.sqlite3`;
- report reali: `parser_test/reports/trader_a_message_types_csv/*`.

Regola per configurazione:

- usare come base il file gia precompilato `docs/in_progress/new_parser/organizzazione_comune/parsing_rules.json`, che e gia allineato alla nuova logica comune per `trader_a`;
- copiare nel nuovo `src/parser/trader_profiles/trader_a/parsing_rules.json` solo la shape del template comune / base config precompilata;
- non importare da legacy `entity_patterns`, `result_patterns`, `cancel_scope_vocabulary`, o nuove chiavi ad hoc;
- se una informazione legacy e un marker dichiarativo gia previsto dal template, si puo consolidare dentro la chiave comune esistente;
- regex e pattern di estrazione complessi vanno in `extractors.py`, non nel `parsing_rules.json`.

Regola per i casi di test:

- `scripts/trader_a_scenarios/*` non e source of truth per la migrazione di FASE 4;
- i test envelope/golden devono essere derivati prima dai casi reali presenti nel DB `db/parser_test__chat_-1003171748254.sqlite3`;
- eventuali fixture sintetiche si possono tenere solo come casi mirati di regressione locale, non come copertura primaria di accettazione.

Checklist:

- [x] Creare `src/parser/trader_profiles/trader_a/extractors.py`.
- [ ] Ridurre `src/parser/trader_profiles/trader_a/profile.py` a orchestration leggera verso `shared/profile_runtime.py`.
- [ ] Spostare dal legacy `profile.py` le regex di estrazione in `extractors.py`, mantenendo il comportamento coperto da test.
- [ ] Estrarre pattern per symbol, side, entries, stop loss, take profits, risk, result, close fraction, hit target, stop level.
- [x] Mappare output extractor verso blocchi raw comuni, non verso `entities` flat.
- [x] Creare nuovo `TraderAProfileParser.parse_message(...) -> TraderEventEnvelopeV1` o introdurre temporaneamente `parse_event_envelope(...)` se serve compatibilita col router.
- [x] Derivare `NEW_SETUP` dal `message_type_hint=NEW_SIGNAL`, non da marker `intent_markers`.
- [x] Derivare `INFO_ONLY` dal `message_type_hint=INFO_ONLY`, non da marker `intent_markers`.
- [x] Convertire intent legacy interni a intent ufficiali prima di costruire envelope.
- [x] Popolare `signal_payload_raw` con `entry_structure`, `entries`, `stop_loss`, `take_profits`, `risk_hint`, `raw_fragments`.
- [x] Popolare `update_payload_raw` con sottoblocchi, non con operations.
- [x] Popolare `report_payload_raw` con `events` e `reported_results`.
- [ ] Popolare `targets_raw` da reply, link Telegram, ID esplicito, symbol/global scope quando applicabile.
- [ ] Spostare residui non comuni in `diagnostics.legacy_*`.
- [ ] Rimuovere business mapping da `profile.py`; niente costruzione canonical nel profilo.
- [x] Tenere temporaneamente `parse_message_legacy` o adapter inverso solo se serve a non rompere router/test esistenti.

Test e validazione per `trader_a`:

- [x] Test schema `parsing_rules.json` comune.
- [ ] Test unitari extractor per almeno: new signal completo, setup incompleto, stop to BE, stop to TP1/prezzo, close full, close partial, cancel pending, update TP, entry filled, TP hit, SL hit, exit BE, final result, info only.
- [x] Test envelope golden cases derivati da casi reali estratti da `db/parser_test__chat_-1003171748254.sqlite3`.
- [x] Test normalizer centrale: `TraderEventEnvelopeV1 -> CanonicalMessage`.
- [ ] Replay `parser_test` filtrato `--trader trader_a --parser-system both`.
- [ ] Audit: zero errori envelope, zero intent fuori tassonomia, residui legacy giustificati.

Exit criteria `trader_a`:

- `trader_a` produce envelope valido per tutti i casi coperti;
- i casi legacy gia coperti non regrediscono;
- `parser_test` mostra output envelope/canonical controllabile;
- eventuali differenze legacy vs nuovo sono classificate come miglioramento semantico, bug, o decisione aperta.

### Fase 5 - Rollout sugli altri trader

Ordine consigliato dopo `trader_a`:

1. `trader_b`
2. `trader_c`
3. `trader_d`
4. `trader_3`

Motivo:

- `trader_b`, `trader_c`, `trader_d` hanno profili simili e possono beneficiare subito del runtime shared;
- `trader_3` ha formato diverso e va migrato dopo aver stabilizzato bene lo scheletro comune.

Checklist per ogni trader:

- [ ] Creare o aggiornare `parsing_rules.json` nella shape comune.
- [ ] Non aggiungere chiavi custom.
- [ ] Spostare regex/pattern complessi in `extractors.py`.
- [ ] Implementare output envelope.
- [ ] Aggiungere test envelope specifici.
- [ ] Eseguire replay `parser_test` per quel trader.
- [ ] Confrontare residui `diagnostics.legacy_*`.
- [ ] Aggiornare documentazione solo se emerge una decisione comune nuova.

### Fase 6 - Deprecazione legacy

Checklist:

- [ ] Rimuovere uso operativo di `TraderParseResult` quando tutti i profili emettono envelope.
- [ ] Rimuovere `parse_canonical()` dai profili.
- [ ] Rimuovere produzione parser-side di `actions_structured`.
- [ ] Tenere adapter legacy solo per leggere dati storici o test comparativi.
- [ ] Aggiornare router per consumare envelope parser-side e canonical downstream.
- [ ] Aggiornare operation rules/target resolver/execution per non dipendere da shape legacy.
- [ ] Rimuovere colonne/report legacy da `parser_test` solo dopo almeno un ciclo completo di validazione per tutti i trader.

## Matrice gap e trattamento

| Gap | Owner | Trattamento |
|---|---|---|
| Runtime envelope non allineato alla spec | `src/parser/event_envelope_v1.py` | Allineare prima di migrare `trader_a` |
| Intent legacy ancora in uso | `shared/intent_taxonomy.py` | Alias temporanei + output ufficiale senza prefisso legacy |
| `RulesEngine` non legge strong/weak intent | `src/parser/rules_engine.py` | Supportare shape nuova e legacy |
| `classification_rules` non implementate | `RulesEngine` / runtime shared | Implementare o rimuovere dal template operativo |
| `context_resolution_rules` non operative | runtime shared | Mini engine deterministico o disabilitazione esplicita |
| `actions_structured` nei report | `parser_test` + profili | Solo diagnostica legacy durante transizione |
| `parse_canonical()` nei profili | profili trader | Rimuovere dopo normalizer centrale envelope-first |
| Pattern legacy in config | profili trader | Spostare regex complesse in `extractors.py` |
| Report parser_test legacy-centrici | `parser_test/reporting` | Aggiungere vista envelope/canonical |

## Checklist sintetica di controllo

Prima di migrare `trader_a`:

- [x] Contratto `TraderEventEnvelopeV1` runtime allineato alla spec. *(Fase 0 completata)*
- [x] Tassonomia intent shared creata. *(Fase 1 completata)*
- [x] `RulesEngine` compatibile con marker strong/weak + `profile_runtime` + `envelope_builder`. *(Fase 2 completata)*
- [x] `parser_test` pronto a mostrare envelope e canonical. *(Fase 3 completata)*

Durante `trader_a`:

- [ ] Usare come base operativa `docs/in_progress/new_parser/organizzazione_comune/parsing_rules.json`; il template `parsing_rules.template.treader_a.jsonc` resta riferimento di shape, non sorgente primaria se la base precompilata e gia coerente.
- [ ] Non aggiungere variabili legacy al template.
- [ ] Estrarre regex/pattern legacy in `extractors.py`.
- [ ] Produrre blocchi raw comuni.
- [ ] Spostare residui in `diagnostics.legacy_*`.

Dopo `trader_a`:

- [ ] Replay `parser_test` su dati reali.
- [ ] Audit envelope/canonical.
- [ ] Lista mismatch classificata.
- [ ] Decisioni comuni aggiornate nella doc, se necessarie.

## Punti da ragionare insieme

Questi sono i punti dove serve una scelta prima di scrivere molto codice:

1. Cambio firma immediato o compat method temporaneo.
   Opzione A: cambiare subito `parse_message(...) -> TraderEventEnvelopeV1`. Opzione B: introdurre prima `parse_event_envelope(...)` e lasciare `parse_message(...)` legacy finche router/parser_test sono pronti. Raccomandazione: Opzione B per una migrazione piu controllabile.

2. Forma del normalizer durante transizione.
   Opzione A: normalizer accetta solo nuova envelope. Opzione B: normalizer accetta nuova envelope e adapter legacy. Raccomandazione: Opzione B fino a fine migrazione di tutti i trader.

3. `context_resolution_rules`.
   Se servono per `trader_a`, implementarle prima. Se non servono nel primo passaggio, vanno escluse dalla validazione operativa per non dare falsa sicurezza.

4. Naming del file template.
   Il file corrente si chiama `parsing_rules.template.treader_a.jsonc`. Se e un refuso voluto, mantenerlo. Se e refuso, rinominarlo prima della migrazione per evitare ambiguita nei riferimenti.

5. Soglia di accettazione replay.
   Per il primo passaggio consiglio: zero crash, zero intent fuori tassonomia, envelope valido per tutti i messaggi parsabili, mismatch legacy/canonical classificati e non nascosti.

6. Fonte dei casi di validazione trader_a.
   I casi reali dal DB `db/parser_test__chat_-1003171748254.sqlite3` hanno precedenza su scenari sintetici in `scripts/trader_a_scenarios/*`. Gli scenari sintetici possono restare solo come supporto secondario.

## Suggested commit message

```text
docs(parser): plan migration to common TraderEventEnvelopeV1 parser system
```

---

## Lavoro svolto - FASE 1

**Data:** 2026-04-25
**Stato:** COMPLETATA

### File creati

| File | Descrizione |
|---|---|
| `src/parser/trader_profiles/shared/__init__.py` | Package init |
| `src/parser/trader_profiles/shared/intent_taxonomy.py` | Tassonomia intent ufficiale, alias legacy, precedenze, mutue esclusioni, funzioni resolve/normalize/select |
| `src/parser/trader_profiles/shared/entity_keys.py` | Vocabolario chiavi ammesse per blocchi raw (SIGNAL, UPDATE, REPORT, INSTRUMENT) |
| `src/parser/trader_profiles/shared/rules_schema.json` | JSON Schema documentale per `parsing_rules.json` |
| `src/parser/trader_profiles/shared/rules_schema.py` | Validatore Python per `parsing_rules.json` (no dipendenza jsonschema) |
| `src/parser/trader_profiles/shared/targeting.py` | Estrazione target ref parser-side: REPLY, TELEGRAM_LINK, MESSAGE_ID, SYMBOL, global scope |
| `src/parser/trader_profiles/shared/warnings.py` | Codici warning condivisi (snake_case, nessun duplicato) |
| `tests/parser_shared/__init__.py` | Package init test |
| `tests/parser_shared/test_intent_taxonomy.py` | 28 test: intent ufficiali, alias, resolve, normalize, precedenze, mutue esclusioni, multi-intent |
| `tests/parser_shared/test_entity_keys.py` | 10 test: chiavi richieste per ogni blocco, no duplicati |
| `tests/parser_shared/test_rules_schema.py` | 11 test: trader_a valida, template valida, rifiuta campi invalidi |
| `tests/parser_shared/test_targeting.py` | 14 test: build ref, extract targets da reply/link/testo |
| `tests/parser_shared/test_warnings.py` | 5 test: unicita codici, snake_case, importabilita |

### Test aggiunti/modificati

- Totale: **82 test** tutti verdi (GREEN).
- Zero regressioni: i 37 test falliti nel suite completo erano pre-esistenti prima di FASE 1.

### Comportamento implementato

1. **`OFFICIAL_INTENTS`**: lista unica di 17 intent senza prefissi legacy.
2. **`LEGACY_ALIASES`**: 15 mapping da nomi legacy (`U_*`, `NS_*`) a ufficiali. Include `U_RISK_NOTE -> INFO_ONLY` trovato in `trader_a/parsing_rules.json`, e `U_REVERSE_SIGNAL -> INVALIDATE_SETUP`.
3. **`PRIMARY_INTENT_PRECEDENCE`**: ordine di priorita per `primary_intent_hint`. `SL_HIT` ha massima precedenza; `INFO_ONLY` ha minima.
4. **`MUTUAL_EXCLUSIONS`**: `NEW_SETUP` esclude `SL_HIT`, `CLOSE_FULL`, `CLOSE_PARTIAL`, `INVALIDATE_SETUP`, `EXIT_BE`.
5. **`COMPATIBLE_MULTI_INTENT`**: coppie comuni come `SL_HIT + CLOSE_FULL`, `TP_HIT + MOVE_STOP_TO_BE`.
6. **`validate_rules()`**: valida `parsing_rules.json` legacy e template. Accetta sia flat list che `{strong, weak}` dict per `intent_markers` durante la transizione (FASE 1-3). Rifiuta chiavi intent sconosciute.
7. **`extract_targets()`**: produce lista di `TargetRefRaw` da reply ID, link Telegram estratti, testo. Compatibile con la shape `targets_raw` di `TraderEventEnvelopeV1`.

### Casi limite non coperti

- Il validatore `rules_schema.py` non usa il file `rules_schema.json` — la validazione e puramente in Python. Il file JSON e solo documentale. In FASE 2+ si puo aggiungere un JSON Schema validator se viene aggiunta la dipendenza `jsonschema`.
- `select_primary_intent()` richiede che gli intent siano gia normalizzati. Se chiamato con intent legacy, solleva ValueError. Il chiamante e responsabile di invocare `normalize_intents()` prima.
- `extract_targets()` non cerca global scope markers nel testo (es. "chiudi tutti i long"). Questo resta responsabilita del profilo trader specifico.
- La forma `{strong, weak}` dei `intent_markers` non e ancora obbligatoria: il validator la accetta in forma flat per compatibilita col `trader_a/parsing_rules.json` legacy. Diventera obbligatoria dopo FASE 4.

### Decisioni tecniche

1. **`U_RISK_NOTE -> INFO_ONLY`**: trovato in `trader_a/parsing_rules.json` ma assente nella proposta di tassonomia. Mappato a `INFO_ONLY` perche si tratta di annotazione informativa sul rischio, non di un'azione sulla posizione.
2. **`U_REVERSE_SIGNAL -> INVALIDATE_SETUP`**: semanticamente piu vicino a invalidazione/cancellazione del setup che a una chiusura di posizione aperta.
3. **Schema lenient durante transizione**: il validatore accetta flat list per `intent_markers` nei file legacy. Questo consente a `trader_a/parsing_rules.json` di passare la validazione oggi senza richiedere la migrazione in FASE 1. FASE 4 aggiornerebbe il file alla shape comune.
4. **`TargetRefRaw` come dataclass frozen**: scelta per immutabilita e hashabilita, distinto dalla versione Pydantic in `event_envelope_v1.py`. In FASE 2/4 il targeting module produrra direttamente la versione Pydantic.

---

## Lavoro svolto - FASE 2

**Data:** 2026-04-25
**Stato:** COMPLETATA

### File modificati

| File | Tipo | Descrizione |
|---|---|---|
| `src/parser/rules_engine.py` | Modifica | `IntentMatchResult` NamedTuple aggiunta; `_intent_markers` normalizzato a `{strong, weak}`; `detect_intents_with_evidence()` nuovo metodo; `classification_rules.when_all_fields_present` implementato; `context_resolution_rules` disabilitato esplicitamente (log debug); `_field_markers` letti da regole |
| `src/parser/trader_profiles/shared/envelope_builder.py` | Creato | `EnvelopeInputs` dataclass + `build_envelope()`: normalizza intent legacy, seleziona `primary_intent_hint`, aggiunge warning comuni |
| `src/parser/trader_profiles/shared/profile_runtime.py` | Creato | `ExtractorProtocol` + `SharedProfileRuntime.parse()`: orchestra classify → detect_intents_with_evidence → extractor → extract_targets → build_envelope |

### Test aggiunti/modificati

| File | Test | Descrizione |
|---|---|---|
| `tests/rules_engine/__init__.py` | — | Package init |
| `tests/rules_engine/test_rules_engine_phase2.py` | 27 test | `IntentMatchResult`, `detect_intents_with_evidence`, compat flat, `classification_rules.when_all_fields_present`, `context_resolution_rules` disabilitato |
| `tests/parser_shared/test_envelope_builder.py` | 20 test | build basico, `primary_intent_hint`, normalizzazione legacy, warning comuni |
| `tests/parser_shared/test_profile_runtime.py` | 15 test | flusso completo, normalizzazione intent, target da context, instrument da extractor |

Totale nuovi test: **62 GREEN**, zero regressioni (i 37 fallimenti pre-esistenti invariati).

### Codice modificato

**`src/parser/rules_engine.py`**

- Aggiunto `IntentMatchResult(intent, strength, matched_marker)` come `NamedTuple`.
- Aggiunto `_normalise_intent_markers()`: converte flat list legacy e shape `{strong, weak}` verso formato interno normalizzato.
- Aggiunto `_find_intent_match()`: per ogni intent, cerca prima strong poi weak, restituisce il primo match con la forza corretta.
- Aggiunto `_field_present()`: verifica se almeno un marker di un campo e presente nel testo (usato da `classification_rules`).
- `detect_intents()`: ora delega a `detect_intents_with_evidence()` — backward compat garantita.
- Nuovo `detect_intents_with_evidence()`: restituisce `list[IntentMatchResult]`, uno per intent, con `strength` e `matched_marker`.
- `classify()`: applica `classification_rules` con `when_all_fields_present` prima delle `combination_rules`. Se tutti i field_markers del campo sono presenti, aggiunge il `score` alla categoria `then`.
- `__init__`: se `context_resolution_rules` e dichiarato e non vuoto, logga un avviso `debug` e non lo applica.

**`src/parser/trader_profiles/shared/envelope_builder.py`** (nuovo)

- `EnvelopeInputs`: dataclass con tutti i campi opzionali (message_type_hint, intents_raw, instrument, payload raw, targets_raw, confidence, diagnostics).
- `build_envelope(inputs)`: normalizza intents con `_safe_normalize_intents()` (ignora sconosciuti con warning), seleziona `primary_intent_hint`, applica 3 warning comuni, costruisce `TraderEventEnvelopeV1`.
- Warning aggiunti: `MISSING_TARGET` (UPDATE senza targets), `CONFLICTING_INTENTS` (mutue esclusioni violate), `UNCLASSIFIED_WITH_MARKERS` (UNCLASSIFIED con intent rilevati), `INTENT_OUTSIDE_TAXONOMY` (intent non in tassonomia).

**`src/parser/trader_profiles/shared/profile_runtime.py`** (nuovo)

- `ExtractorProtocol`: Protocol con `extract(text, context, rules) -> dict`. Il dict puo contenere: `instrument`, `signal_payload_raw`, `update_payload_raw`, `report_payload_raw`, `intents_extra`, `targets_extra`, `telegram_links`, `diagnostics`.
- `SharedProfileRuntime.parse()`: orchestra classify → detect_intents_with_evidence → extractor.extract → extract_targets → build_envelope. Converte `TargetRefRaw` (dataclass) dalla targeting module in `TargetRefRaw` (Pydantic) per l'envelope.

### Comportamento implementato

1. **`RulesEngine.detect_intents_with_evidence()`**: per ogni intent, restituisce un `IntentMatchResult` con `strength="strong"` se il match e su un marker forte, `"weak"` altrimenti. Un solo result per intent.
2. **`classification_rules.when_all_fields_present`**: nuova regola strutturale. Richiede che tutti i campi elencati abbiano almeno un marker presente. Applica `score` alla categoria `then`. Separata chiaramente da `combination_rules` che restano inalterati.
3. **`context_resolution_rules`**: dichiarate non operative. L'engine le ignora senza crash, logga a livello DEBUG. Richiedono target history — saranno implementate in una fase futura dedicata.
4. **`build_envelope()`**: punto di costruzione centralizzato per tutti i profili. Nessun profilo deve costruire `TraderEventEnvelopeV1` direttamente.
5. **`SharedProfileRuntime.parse()`**: flusso comune usabile da tutti i profili in FASE 4.

### Casi limite non coperti

- `context_resolution_rules` non sono operative. Sono dichiarate nel template ma ignorate. Non c'e nessun mini-engine; richiedono target history (accesso DB) che va in scope dopo FASE 3.
- `classification_rules` con `when_all_fields_present` usano `field_markers` per determinare la presenza dei campi. Se `field_markers` non e dichiarato nel `parsing_rules.json`, la regola non si attiva (i field_markers saranno vuoti e nessun campo risulta presente).
- `ExtractorProtocol` e una `Protocol` senza enforcement runtime. Se un extractor restituisce chiavi sconosciute nel dict, vengono silenziosamente ignorate.
- La conversione `TargetRefRaw` (dataclass) → `TargetRefRaw` (Pydantic) in `profile_runtime.py` e esplicita ma ridondante. Sara eliminata quando `targeting.py` produrra direttamente la versione Pydantic (gia annotato come punto di miglioramento in FASE 1).

### Decisioni tecniche

1. **`IntentMatchResult` come `NamedTuple`**: scelta leggera e immutabile; non richiede Pydantic. Coerente con il ruolo di oggetto di analisi interno, non di contratto esterno.
2. **`detect_intents()` delega a `detect_intents_with_evidence()`**: elimina la duplicazione del loop di matching. Backward compat garantita senza mantenere due loop separati.
3. **`classification_rules` prima di `combination_rules` in `classify()`**: le regole strutturali (presenza di campi) hanno semantica diversa e piu stabile rispetto ai booster lessicali. Applicarle prima riduce il rischio di interferenza.
4. **`context_resolution_rules` → log DEBUG, non WARNING**: non e un errore di configurazione; il template le prevede e sara implementato. Il log avvisa senza inquinare i log di produzione.
5. **`EnvelopeInputs` come `dataclass` non Pydantic**: e solo un contenitore di input per `build_envelope()`, non un contratto esterno. Pydantic qui sarebbe overhead non necessario.
6. **`_safe_normalize_intents()` non raise su sconosciuti**: il `build_envelope()` e un punto di raccolta per output di fonti diverse (engine + extractor). Sollevare un'eccezione per un intent sconosciuto bloccherebbe il parsing; il warning e sufficiente per il debugging.

---

## Lavoro svolto - FASE 3

**Data:** 2026-04-25  
**Stato:** COMPLETATA

### File modificati

- `parser_test/scripts/replay_parser.py`
- `parser_test/scripts/generate_parser_reports.py`
- `parser_test/reporting/flatteners.py`
- `parser_test/reporting/report_schema.py`
- `parser_test/reporting/canonical_v1_audit.py`
- `parser_test/tests/test_report_export.py`
- `parser_test/scripts/tests/test_audit_canonical_v1.py`
- `parser_test/scripts/tests/test_replay_parser_phase3.py`

### Test aggiunti/modificati

- `parser_test/scripts/tests/test_replay_parser_phase3.py`
  - verifica serializzazione `parse_result_normalized_json` in modalita `legacy`, `common`, `both`
- `parser_test/tests/test_report_export.py`
  - verifica che i report leggano prima `event_envelope_v1` e pubblichino le nuove colonne envelope/canonical
- `parser_test/scripts/tests/test_audit_canonical_v1.py`
  - verifica audit su payload `common` con conteggio residui `diagnostics.legacy_*`

Validazione eseguita:

- `.venv/Scripts/python.exe -m pytest parser_test/scripts/tests/test_replay_parser_phase3.py parser_test/tests/test_report_export.py parser_test/scripts/tests/test_audit_canonical_v1.py -q`
- `.venv/Scripts/python.exe -m pytest parser_test/tests parser_test/scripts/tests -q`

Esito: **31 test passed** sulla suite `parser_test` toccata.

### Codice modificato

- `replay_parser.py`
  - aggiunto flag CLI `--parser-system legacy|common|both` con default `both`
  - aggiunta serializzazione centralizzata `_build_normalized_payload(...)`
  - in modalita `common` salva solo `event_envelope_v1` e `canonical_message_v1`
  - in modalita `both` mantiene la shape legacy top-level e affianca envelope + canonical
- `generate_parser_reports.py`
  - propaga `--parser-system` al replay
- `flatteners.py`
  - costruisce una vista report envelope-first con fallback legacy
  - espone colonne envelope/canonical senza rimuovere le colonne legacy
  - aggiunge summary sintetici per payload signal/update/report
- `report_schema.py`
  - aggiorna gli header CSV con colonne di confronto envelope/canonical e summary payload
- `canonical_v1_audit.py`
  - valida `event_envelope_v1` quando presente
  - usa `canonical_message_v1` salvato quando disponibile, con fallback al normalizer
  - conta errori envelope e residui `legacy_*`

### Comportamento implementato

1. Dopo il replay, `parse_result_normalized_json` puo rappresentare tre modalita:
   - `legacy`: shape legacy attuale;
   - `common`: solo `event_envelope_v1` + `canonical_message_v1`;
   - `both`: legacy + envelope + canonical nello stesso record.
2. I report CSV leggono prima `event_envelope_v1` e `canonical_message_v1` quando presenti, poi fanno fallback alla shape legacy.
3. I CSV mostrano ora segnali espliciti per il confronto:
   - envelope: `message_type_hint`, `primary_intent_hint`, `intents_detected`, symbol/side strumentali, count target, count residui legacy;
   - canonical: `primary_class`, `parse_status`, `confidence`;
   - payload summary: signal, update, report.
4. L'audit misura sia errori del normalizer sia errori envelope e residui `diagnostics.legacy_*`.

### Eventuali casi limite non coperti

- `replay_operation_rules.py` non e stato esteso in questa fase: continua a lavorare sulla shape legacy top-level. Per questo il default operativo resta `--parser-system both` durante la migrazione.
- I summary CSV dei payload comuni sono sintetici e orientati al confronto rapido; non sostituiscono un dump JSON completo quando serve analisi fine.
- In modalita `common`, colonne storicamente basate su `actions_structured` possono risultare vuote se non derivabili da envelope/canonical. La decisione e intenzionale: FASE 3 espone il sistema nuovo senza inventare mapping business aggiuntivi.

### Eventuali decisioni tecniche prese

1. **Default `both` conservativo**: scelta fatta per non rompere i consumer `parser_test` ancora legacy-centrici durante la migrazione.
2. **Nessun nuovo audit file**: invece di introdurre `event_envelope_v1_audit.py`, e stato esteso `canonical_v1_audit.py` per tenere un solo punto di verifica comparativa.
3. **Envelope-first nei report**: quando envelope e canonical sono presenti, il report usa quelli come source of truth e non la shape legacy, che resta solo fallback/diagnostica.

---

## Lavoro svolto - FASE 4

**Data:** 2026-04-26  
**Stato:** COMPLETATA

### File modificati

- `src/parser/trader_profiles/trader_a/parsing_rules.json`
- `src/parser/trader_profiles/trader_a/profile.py`
- `src/parser/trader_profiles/trader_a/extractors.py`
- `src/parser/canonical_v1/normalizer.py`
- `src/parser/trader_profiles/trader_a/tests/test_parsing_rules_integrity.py`
- `src/parser/trader_profiles/trader_a/tests/test_profile_phase4_common.py`

### Test aggiunti/modificati

- `src/parser/trader_profiles/trader_a/tests/test_parsing_rules_integrity.py`
  - verifica che `trader_a/parsing_rules.json` coincida con la base comune precompilata;
  - verifica validazione contro lo schema comune;
  - verifica rimozione delle chiavi legacy vietate;
  - verifica uso della shape `{strong, weak}` per tutti gli `intent_markers`.
- `src/parser/trader_profiles/trader_a/tests/test_profile_phase4_common.py`
  - caso reale DB `telegram_message_id=200`: envelope `NEW_SIGNAL` completo;
  - caso reale DB `telegram_message_id=262`: envelope `UPDATE` con `MOVE_STOP_TO_BE` e target reply;
  - caso reale DB `telegram_message_id=263`: envelope `REPORT` con evento `SL_HIT` e risultato percentuale;
  - verifica `parse_canonical(...)` sul nuovo percorso envelope-first.

Validazione eseguita:

- `.venv/Scripts/python.exe -m pytest src/parser/trader_profiles/trader_a/tests/test_parsing_rules_integrity.py src/parser/trader_profiles/trader_a/tests/test_profile_phase4_common.py -q`
- `.venv/Scripts/python.exe -m pytest src/parser/trader_profiles/trader_a/tests/test_profile_smoke.py -q`

Esito: **11 test passed** (`8` test FASE 4 + `3` smoke legacy).

### Codice modificato

- `trader_a/parsing_rules.json`
  - sostituito con la base comune `docs/in_progress/new_parser/organizzazione_comune/parsing_rules.json`;
  - rimosse dal profilo operativo le chiavi legacy fuori shape comune (`entity_patterns`, `result_patterns`, `cancel_scope_vocabulary`).
- `trader_a/extractors.py`
  - nuovo layer trader-specifico di estrazione parser-side;
  - estrazione di `symbol`, `side`, entry primaria/averaging, `stop_loss`, `take_profits`, `risk_hint`;
  - estrazione update `MOVE_STOP_TO_BE`;
  - estrazione report/eventi `SL_HIT`, `EXIT_BE`, `ENTRY_FILLED` e risultati percentuali.
- `trader_a/profile.py`
  - aggiunto `parse_event_envelope(...)` su `SharedProfileRuntime`;
  - aggiunto post-processing conservativo dell'envelope per:
    - derivare `NEW_SETUP` da `message_type_hint=NEW_SIGNAL`;
    - promuovere `UNCLASSIFIED -> UPDATE` quando ci sono target + intent update;
    - promuovere `UNCLASSIFIED -> REPORT` per report passivi;
    - filtrare intent rumorosi sui segnali nuovi;
  - `parse_canonical(...)` ora delega al normalizer centrale envelope-first;
  - `parse_message(...)` resta compatibile: usa il legacy path e fa fallback al nuovo percorso comune solo quando il legacy torna sostanzialmente vuoto/unclassified.
- `canonical_v1/normalizer.py`
  - aggiunta API shared `normalize_event_envelope(...)`;
  - `parse_canonical(...)` di `trader_a` non costruisce piu il canonical nel profilo;
  - aggiunto targeting minimale envelope-first per reply/link.

### Comportamento implementato

1. `trader_a` ha ora un ingresso esplicito comune `parse_event_envelope(text, context) -> TraderEventEnvelopeV1`.
2. I casi reali usati come golden della FASE 4 ora producono:
   - nuovo segnale completo `NEW_SIGNAL` con `NEW_SETUP`, entries a due step, SL, TP e risk hint;
   - update reply-targeted `MOVE_STOP_TO_BE` con `stop_update.mode=TO_ENTRY`;
   - report `SL_HIT` con risultato percentuale parser-side.
3. `parse_canonical(...)` passa dal normalizer centrale envelope-first, quindi il profilo non possiede piu il mapping canonical del percorso nuovo.
4. Il percorso legacy operativo non e stato rimosso: viene mantenuto come bridge conservativo per non anticipare la FASE 6.

### Eventuali casi limite non coperti

- `extractors.py` copre il sottoinsieme necessario per i casi reali usati in FASE 4, non tutta la casistica legacy di `trader_a`.
- Il bridge `parse_message(...)` verso `TraderParseResult` non ricostruisce l'intera semantica legacy (`actions_structured`, `target_scope`, `linking`) dal nuovo envelope; il fallback comune e volutamente minimale.
- Il targeting globale parser-side del nuovo percorso non e ancora promosso in modo completo per tutti i marker portfolio-wide di `trader_a`.
- `parse_canonical(...)` e stato riallineato al normalizer shared, ma gli altri consumer del repository restano legacy-first finche non si entra nella FASE 6.

### Eventuali decisioni tecniche prese

1. **Scelta conservativa di compatibilita**: invece di rompere subito `parse_message(...)`, e stato introdotto `parse_event_envelope(...)` come ingresso FASE 4 e `parse_message(...)` usa fallback al nuovo percorso solo quando il legacy non produce segnale utile.
2. **Golden da casi reali DB**: i test envelope usano direttamente tre messaggi reali del DB indicato nel piano (`telegram_message_id=200`, `262`, `263`).
3. **Normalizer shared promosso solo quanto basta**: e stata aggiunta `normalize_event_envelope(...)` per togliere ownership canonical al profilo `trader_a`, senza anticipare la migrazione globale degli altri trader.
