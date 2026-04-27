# Report Structure Proposal

Obiettivo: trasformare i CSV di `parser_test` in report **common-only**, cioe basati su `TraderEventEnvelopeV1` + `canonical_message_v1`, senza mescolare semantiche legacy nel CSV principale.

## Principi

- Separare chiaramente dati operativi, dati di classificazione e diagnostica.
- Tenere nella vista principale solo colonne che servono per valutare efficacia e comportamento del parser nuovo.
- Spostare i dettagli tecnici envelope/canonical in colonne debug opzionali.
- Evitare duplicazioni tra `raw`, `normalized`, `envelope`, `canonical` e `actions_structured`.
- Rendere `entries_summary` e `reported_results` semanticamente coerenti, non solo serializzazioni di fallback.

## Problemi Dell'attuale Struttura

- Troppe colonne raccontano la stessa cosa da prospettive diverse.
- `entries_summary` usa fallback eterogenei e puo cambiare significato tra record.
- `reported_results` mescola viste diverse invece di riflettere un contratto unico.
- Le colonne envelope/canonical sono utili per debug, ma affollano la vista principale.
- `raw_text` e i dettagli tecnici rendono difficile leggere il CSV come report, non come dump.

## Struttura Proposta Common-Only

### A. Vista Principale

Questa e la vista da usare per valutare il parser nuovo e produrre report standard.

| Colonna | Ruolo |
|---|---|
| `raw_message_id` | Identita riga. |
| `parse_status` | Stato del parsing nel DB. |
| `parser_system` | Deve essere `common` oppure `both` con prevalenza common. |
| `message_type` | Classificazione finale del messaggio. |
| `primary_intent` | Intent principale. |
| `intents` | Lista intent rilevati. |
| `symbol` | Strumento. |
| `direction` | Direzione della posizione. |
| `market_type` | Tipo di mercato / setup. |
| `status` | Stato operativo del messaggio. |
| `confidence` | Fiducia numerica. |
| `completeness` | Completo o incompleto per signal. |
| `entry_plan_type` | Tipo piano entry. |
| `entry_structure` | Struttura entry normalizzata. |
| `has_averaging_plan` | Presenza averaging. |
| `entry_count` | Numero leg entry. |
| `entries_summary` | Sintesi entry normalizzata. |
| `stop_loss_price` | Stop loss finale. |
| `tp_count` | Numero take profit. |
| `tp_prices` | Prezzi take profit. |
| `signal_id` | Identificatore del segnale. |
| `target_refs_count` | Numero riferimenti target. |
| `reported_results` | Risultati finali sintetizzati. |
| `reported_profit_percent` | Profitto percentuale. |
| `reported_leverage_hint` | Leverage hint se presente. |
| `warnings_summary` | Warning filtrati utili. |
| `validation_warning_count` | Conteggio warning di validazione. |
| `notes_summary` | Note operative sintetiche. |
| `diagnostics_summary` | Diagnostica compatta. |

### Contracto Operativo

- Il CSV principale non deve dipendere da `parse_message_legacy(...)`.
- Se un campo non esiste nel common path, resta vuoto o va spostato nel debug, non deve essere ricostruito con semantica legacy.
- Le colonne devono riflettere il payload common corrente, non un merge con il vecchio `TraderParseResult`.

### B. Vista Targeting E Update

Questa vista puo restare separata o essere inclusa solo in `UPDATE` / `INFO_ONLY`.

| Colonna | Ruolo |
|---|---|
| `target_scope_kind` | Tipo scope target. |
| `target_scope_scope` | Valore scope target. |
| `target_refs` | Riferimenti target. |
| `linking_strategy` | Strategia di linking. |
| `new_stop_level` | Stop aggiornato. |
| `close_scope` | Scope chiusura. |
| `close_fraction` | Frazione chiusa. |
| `hit_target` | Target colpito. |
| `fill_state` | Stato fill. |
| `result_mode` | Modalita risultato. |
| `cancel_scope` | Scope cancellazione. |

### C. Vista Debug Opzionale

Queste colonne devono essere attivabili con flag, non presenti nella vista standard.

| Colonna | Ruolo |
|---|---|
| `raw_text` | Testo grezzo originale. |
| `action_types` | Tipi azione strutturata. |
| `actions_structured_summary` | Sintesi leggibile delle azioni. |
| `envelope_message_type_hint` | Hint envelope. |
| `envelope_primary_intent_hint` | Hint envelope. |
| `envelope_intents_detected` | Intent rilevati nell'envelope. |
| `envelope_instrument_symbol` | Strumento nell'envelope. |
| `envelope_instrument_side` | Lato nell'envelope. |
| `envelope_targets_raw_count` | Conteggio target raw. |
| `envelope_legacy_diagnostics_count` | Diagnostica legacy nell'envelope. |
| `canonical_primary_class` | Classe primaria canonical. |
| `canonical_parse_status` | Stato parse canonical. |
| `canonical_confidence` | Confidence canonical. |
| `signal_payload_summary` | Sintesi payload signal. |
| `update_payload_summary` | Sintesi payload update. |
| `report_payload_summary` | Sintesi payload report. |
| `normalized_json_debug` | JSON completo normalizzato. |

## Colonne Da Unificare O Ridurre

Queste colonne oggi aggiungono rumore o duplicano concetti.

- `message_class` e `event_type`: tenere solo se c'e una distinzione reale nei consumer; altrimenti scegliere uno standard unico.
- `status`: mantenere solo se ha significato operativo distinto da `message_type` e `parse_status`.
- `market_type`: tenere se usato per analisi; altrimenti spostarlo in debug o derivarlo.
- `links_count` e `hashtags_count`: utili per osservabilita, ma non sono centrali nel report signal-first.
- `envelope_*` e `canonical_*`: ottimi per diagnosi, non per la vista operativa.

## Colonne Da Mantenere Con Priorita Alta

- `raw_message_id`
- `parse_status`
- `parser_system`
- `message_type`
- `primary_intent`
- `intents`
- `symbol`
- `direction`
- `entry_structure`
- `entry_count`
- `entries_summary`
- `stop_loss_price`
- `tp_count`
- `tp_prices`
- `signal_id`
- `reported_results`
- `reported_profit_percent`
- `reported_leverage_hint`
- `warnings_summary`
- `validation_warning_count`
- `diagnostics_summary`

## Regole Di Trasformazione Raccomandate

### `entries_summary`

La colonna deve rappresentare una sola semantica common:

- se il parser produce leg entry strutturate, usare quelle;
- se il common payload ha un range esplicito, mostrarlo come `RANGE_LOW` / `RANGE_HIGH`;
- evitare di convertire automaticamente `entities.entry` in `RANGE_LOW` / `RANGE_HIGH` se esistono anche `entry_plan_entries` o `actions_structured.entries` piu precise.

Proposta di formato:

- `PRIMARY:MARKET:0.1493`
- `AVERAGING:LIMIT:0.1577`
- `TP1:LIMIT:0.1620`

### `reported_results`

La colonna deve derivare da una sola vista primaria per record.

Regola proposta:

- prima `canonical.report`;
- poi `envelope.report_payload_raw`;
- mai da `legacy` nel CSV principale.

### `signal_id`

La regola dovrebbe essere esplicita e non dipendere troppo dai fallback impliciti.

Preferenza proposta:

- `entities.signal_id` se presente;
- `normalized.root_ref` per `NEW_SIGNAL`;
- `target_scope.root_ref`;
- `linking.root_ref`;
- primo `target_ref` singolo;
- altrimenti vuoto.

## Struttura CSV Suggerita

### 1. `*_all_messages.csv`

Vista completa ma ordinata in sezioni:

- identity
- classification
- signal core
- targeting/update/result
- diagnostics

### 2. `*_new_signal.csv`

Vista piu stretta, focalizzata su setup e execution readiness.

### 3. `*_update.csv`

Vista focalizzata su modifiche al segnale e gestione posizione.

### 4. `*_info_only.csv`

Vista per messaggi informativi / report finali.

### 5. `*_setup_incomplete.csv`

Vista per analisi dei segnali incompleti.

### 6. `*_unclassified.csv`

Vista minima, usata per triage.

## Ordine Di Migrazione Suggerito

1. Congelare il contratto della vista principale common-only.
2. Spostare `envelope_*` e `canonical_*` in debug opzionale.
3. Rendere `entries_summary` un campo semantico unico common.
4. Separare meglio `reported_results` dal resto.
5. Ridurre le colonne duplicate o poco usate.
6. Aggiornare test di export/report per il nuovo contratto.

## Decisione Consigliata

Per il prossimo passo, io terrei:

- una vista principale compatta e signal-first;
- una vista debug opzionale completa;
- una semantica unica per entry, targeting e reported results.

Questo rende il CSV piu stabile per analisi umana e piu semplice da confrontare tra diversi replay del parser nuovo.
