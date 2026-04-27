# Report Columns Map

Questa mappa descrive la struttura attuale dei CSV di `parser_test`.

Fonte principale del contenuto:
- `parser_test/reporting/report_export.py`
- `parser_test/reporting/flatteners.py`
- `parser_test/reporting/report_schema.py`

Legenda breve:
- `raw_messages` = tabella SQLite dei messaggi grezzi
- `parse_results` = tabella SQLite dei risultati di parsing
- `normalized` = JSON salvato in `parse_results.parse_result_normalized_json`
- `envelope` = `normalized["event_envelope_v1"]`
- `canonical` = `normalized["canonical_message_v1"]`

## Flusso Dati

1. Il report legge `raw_messages` + `parse_results`.
2. `report_export.py` carica `parse_result_normalized_json` e lo passa a `build_report_row(...)`.
3. `flatteners.py` costruisce una vista report con priorita `envelope -> canonical -> normalized`.
4. Le colonne CSV sono poi riempite con valori diretti, fallback, o trasformazioni sintetiche.

## Colonne Comuni A Tutti Gli Scope

| Colonna | Origine | Trasformazione / fallback |
|---|---|---|
| `raw_message_id` | `raw_messages.raw_message_id` | Valore diretto. |
| `parse_status` | `parse_results.parse_status` | Valore diretto. |
| `reply_to_message_id` | `raw_messages.reply_to_message_id` | Vuoto se `NULL`. |
| `raw_text` | `raw_messages.raw_text` | Valore diretto. |
| `warning_text` | `parse_results.warning_text` | Valore diretto. |
| `warnings_summary` | `normalized.warnings` dopo `_filtered_warnings(...)` | Filtra warning legati a update non coerenti con `message_type` e alcuni warning globali; poi join con ` | `. |
| `primary_intent` | `report_view.primary_intent` | Fallback a primo intent disponibile in `report_view.intents`. |
| `intents` | `report_view.intents` | Join dei nomi intent estratti da stringhe o dict `{name, kind}`. |
| `action_types` | `report_view.actions_structured` | Join dei `action_type` distinti presenti nella lista. |
| `actions_structured_summary` | `report_view.actions_structured` | Sintesi leggibile di ogni azione con campi come `intent`, `symbol`, `side`, `entries`, `take_profits`, ecc. |
| `parser_system` | `normalized.parser_system` | Valore diretto (`legacy`, `common`, `both`). |
| `envelope_message_type_hint` | `envelope.message_type_hint` | Valore diretto. |
| `envelope_primary_intent_hint` | `envelope.primary_intent_hint` | Valore diretto. |
| `envelope_intents_detected` | `envelope.intents_detected` | Join della lista. |
| `envelope_instrument_symbol` | `envelope.instrument.symbol` | Valore diretto dal payload envelope. |
| `envelope_instrument_side` | `envelope.instrument.side` | Valore diretto dal payload envelope. |
| `envelope_targets_raw_count` | `envelope.targets_raw` | Conteggio elementi della lista. |
| `envelope_legacy_diagnostics_count` | `envelope.diagnostics` | Conta le chiavi che iniziano con `legacy_`. |
| `canonical_primary_class` | `canonical.primary_class` | Valore diretto. |
| `canonical_parse_status` | `canonical.parse_status` | Valore diretto. |
| `canonical_confidence` | `canonical.confidence` | Formattato come float testuale. |
| `signal_payload_summary` | `envelope.signal_payload_raw` | Sintesi `entries=<n>; stop=<price>; tp=<n>`. |
| `update_payload_summary` | `envelope.update_payload_raw` | Sintesi dei blocchi `stop`, `close`, `cancel`, `entry`, `targets`. |
| `report_payload_summary` | `envelope.report_payload_raw` | Sintesi degli eventi e dei reported results. |

## Colonne Scope `ALL`

`ALL` contiene tutte le colonne comuni piu queste colonne aggiuntive.

| Colonna | Origine | Trasformazione / fallback |
|---|---|---|
| `event_type` | `normalized.event_type` | Valore diretto. |
| `message_class` | `normalized.message_class` | Valore diretto. |
| `symbol` | `normalized.symbol` -> `entities.symbol` | Primo valore non vuoto. |
| `direction` | `normalized.direction` -> `entities.side` -> `entities.direction` | Primo valore non vuoto. |
| `market_type` | `normalized.market_type` | Valore diretto. |
| `status` | `normalized.status` | Valore diretto. |
| `confidence` | `normalized.confidence` | Formattato come float testuale. |
| `parser_used` | `normalized.parser_used` | Valore diretto. |
| `parser_mode` | `normalized.parser_mode` | Valore diretto. |
| `entry_plan_type` | `normalized.entry_plan_type` -> `normalized.entry_plan.entry_plan_type` -> `entities.entry_plan_type` | Primo valore non vuoto. |
| `entry_structure` | `normalized.entry_structure` -> `normalized.entry_plan.entry_structure` -> `entities.entry_structure` | Primo valore non vuoto. |
| `has_averaging_plan` | `normalized.has_averaging_plan` -> `normalized.entry_plan.has_averaging_plan` -> `entities.has_averaging_plan` | Convertito in `True` / `False`. |
| `entry_count` | `_coerce_entries(...)` | `len(entries)` dopo la priorita delle fonti. |
| `entries_summary` | `_coerce_entries(...)` | `role:order_type:price` unito con ` | `. |
| `stop_loss_price` | `entities.stop_loss` -> `normalized.stop_loss_price` -> `risk_plan.stop_loss.price` | Primo valore numerico disponibile, poi formattato. |
| `tp_prices` | `entities.take_profits` -> `actions_structured.take_profits` -> `risk_plan.take_profits` -> `normalized.take_profit_prices` -> `entities.take_profit_prices` | Join dei prezzi numerici. |
| `tp_count` | Come `tp_prices` | `len(take_profits)`. |
| `signal_id` | `entities.signal_id` -> fallback derivato | Se `NEW_SIGNAL`, usa `root_ref` o `raw_message_id`; altrimenti prova `root_ref`, `target_scope.root_ref`, `linking.root_ref`, o il primo `target_ref` singolo. |
| `target_scope_kind` | `normalized.target_scope` | Se mancano valori espliciti, deriva da `target_refs` o da `message_type=UPDATE`. |
| `target_scope_scope` | `normalized.target_scope` | Normalizza `single` / `multiple` / `unknown`. |
| `target_refs` | `_canonical_target_refs(...)` | Unifica refs da `actions_structured`, `target_scope`, `linking`, `normalized`, `entities`, `root_ref`, `target_ref`; poi join numerico. |
| `target_refs_count` | Come `target_refs` | `len(target_refs)`. |
| `linking_strategy` | `normalized.linking.strategy` -> `normalized.linking.mode` | Conversione a stringa lower-case. |
| `new_stop_level` | Primo `actions_structured[*].new_stop_level` -> `entities.new_stop_level` | Primo valore non vuoto. |
| `close_scope` | Primo `actions_structured[*].close_scope` -> `entities.close_scope` | Primo valore non vuoto. |
| `close_fraction` | Primo `actions_structured[*].close_fraction` -> `entities.close_fraction` | Formattato come float testuale. |
| `hit_target` | Primo `actions_structured[*].hit_target` -> `entities.hit_target` | Primo valore non vuoto. |
| `fill_state` | Primo `actions_structured[*].fill_state` -> `entities.fill_state` | Primo valore non vuoto. |
| `result_mode` | Primo `actions_structured[*].result_mode` -> `entities.result_mode` | Primo valore non vuoto. |
| `cancel_scope` | Primo `actions_structured[*].cancel_scope` -> `entities.cancel_scope` | Primo valore non vuoto. |
| `reported_results` | `_reported_results_from_common_views(...)` + `_coerce_reported_results(...)` | Preferisce `envelope.report_payload_raw.reported_results`, poi `canonical.report.reported_result`, poi `normalized.reported_results`; sintetizza come `SYMBOL:VALUEUNIT`. |
| `reported_profit_percent` | `entities.reported_profit_percent` -> `results_v2` | Primo valore numerico con `unit=PERCENT`. |
| `reported_leverage_hint` | `entities.reported_leverage_hint` -> `results_v2` | Primo valore numerico disponibile. |
| `notes_summary` | `normalized.notes` -> `normalized.parsing_notes` | Join della lista. |
| `links_count` | `entities.links` -> `normalized.links` | Conteggio della prima lista non vuota. |
| `hashtags_count` | `entities.hashtags` -> `normalized.hashtags` | Conteggio della prima lista non vuota. |
| `validation_warning_count` | `normalized.validation_warnings` | Conteggio elementi. |
| `diagnostics_summary` | `normalized.diagnostics` | Estrae solo `parser_mode`, `parser_used`, `confidence`, `parse_status_input`, `intents_count`, `actions_count`, `warning_count`. |

## Colonne Scope `NEW_SIGNAL`

| Colonna | Origine | Trasformazione / fallback |
|---|---|---|
| `symbol` | come sopra | idem. |
| `direction` | come sopra | idem. |
| `market_type` | come sopra | idem. |
| `completeness` | `normalized.completeness` | Valore diretto. |
| `confidence` | `normalized.confidence` | Formattato float. |
| `parser_used` | `normalized.parser_used` | Valore diretto. |
| `parser_mode` | `normalized.parser_mode` | Valore diretto. |
| `status` | `normalized.status` | Valore diretto. |
| `entry_plan_type` | come `ALL` | Primo valore non vuoto tra normalized / entry_plan / entities. |
| `entry_structure` | come `ALL` | Primo valore non vuoto tra normalized / entry_plan / entities. |
| `has_averaging_plan` | come `ALL` | Bool scalar. |
| `entry_count` | come `ALL` | `len(entries)`. |
| `entries_summary` | come `ALL` | `role:order_type:price`. |
| `stop_loss_price` | come `ALL` | Fallback numerico. |
| `tp_prices` | come `ALL` | Join prezzi. |
| `tp_count` | come `ALL` | Conteggio. |
| `signal_id` | come `ALL` | Derivazione da ref / root. |
| `target_refs` | come `ALL` | Join refs. |
| `target_refs_count` | come `ALL` | Conteggio. |
| `linking_strategy` | come `ALL` | Lower-case. |
| `notes_summary` | `normalized.notes` -> `normalized.parsing_notes` | Join. |
| `links_count` | come `ALL` | Conteggio. |
| `hashtags_count` | come `ALL` | Conteggio. |
| `validation_warning_count` | come `ALL` | Conteggio. |
| `diagnostics_summary` | come `ALL` | Sintesi selettiva. |

## Colonne Scope `UPDATE`

| Colonna | Origine | Trasformazione / fallback |
|---|---|---|
| `symbol` | come sopra | idem. |
| `direction` | come sopra | idem. |
| `market_type` | come sopra | idem. |
| `confidence` | `normalized.confidence` | Formattato float. |
| `parser_used` | `normalized.parser_used` | Valore diretto. |
| `parser_mode` | `normalized.parser_mode` | Valore diretto. |
| `status` | `normalized.status` | Valore diretto. |
| `signal_id` | come sopra | Derivato da refs / root. |
| `target_scope_kind` | come sopra | Normalizzato da scope legacy. |
| `target_scope_scope` | come sopra | Normalizzato da scope legacy. |
| `target_refs` | come sopra | Join refs. |
| `target_refs_count` | come sopra | Conteggio. |
| `linking_strategy` | come sopra | Lower-case. |
| `new_stop_level` | come sopra | Primo action field. |
| `close_scope` | come sopra | Primo action field. |
| `close_fraction` | come sopra | Primo action field, float. |
| `hit_target` | come sopra | Primo action field. |
| `fill_state` | come sopra | Primo action field. |
| `result_mode` | come sopra | Primo action field. |
| `cancel_scope` | come sopra | Primo action field. |
| `reported_results` | come sopra | Sintesi risultati. |
| `reported_profit_percent` | come sopra | Prende `PERCENT` da results. |
| `reported_leverage_hint` | come sopra | Prende leverage hint da results. |
| `tp_prices` | come sopra | Join prezzi. |
| `links_count` | come sopra | Conteggio. |
| `hashtags_count` | come sopra | Conteggio. |
| `validation_warning_count` | come sopra | Conteggio. |
| `diagnostics_summary` | come sopra | Sintesi selettiva. |

## Colonne Scope `INFO_ONLY`

| Colonna | Origine | Trasformazione / fallback |
|---|---|---|
| `symbol` | come sopra | idem. |
| `direction` | come sopra | idem. |
| `signal_id` | come sopra | Derivato da refs / root. |
| `linking_strategy` | come sopra | Lower-case. |
| `reported_results` | come sopra | Sintesi risultati. |
| `reported_profit_percent` | come sopra | Valore percentuale. |
| `reported_leverage_hint` | come sopra | Leverage hint. |
| `notes_summary` | `normalized.notes` -> `normalized.parsing_notes` | Join. |
| `target_refs_count` | come sopra | Conteggio. |
| `links_count` | come sopra | Conteggio. |
| `hashtags_count` | come sopra | Conteggio. |
| `validation_warning_count` | come sopra | Conteggio. |
| `diagnostics_summary` | come sopra | Sintesi selettiva. |

## Colonne Scope `SETUP_INCOMPLETE`

| Colonna | Origine | Trasformazione / fallback |
|---|---|---|
| `symbol` | come sopra | idem. |
| `direction` | come sopra | idem. |
| `completeness` | `normalized.completeness` | Valore diretto. |
| `missing_fields` | `normalized.missing_fields` | Join della lista. |
| `entry_plan_type` | come sopra | Primo valore non vuoto. |
| `entry_structure` | come sopra | Primo valore non vuoto. |
| `has_averaging_plan` | come sopra | Bool scalar. |
| `missing_stop_flag` | calcolato da `normalized.stop_loss_price` + `risk_plan.stop_loss.price` | `True` se stop assente in entrambe le viste. |
| `missing_tp_flag` | calcolato da `entries`/`take_profits` | `True` se nessun take profit. |
| `missing_entry_flag` | calcolato da `entries` + `normalized.entry_main` | `True` se nessun entry e manca `entry_main`. |
| `validation_warning_count` | `normalized.validation_warnings` | Conteggio. |
| `notes_summary` | `normalized.notes` -> `normalized.parsing_notes` | Join. |
| `diagnostics_summary` | `normalized.diagnostics` | Sintesi selettiva. |

## Colonne Scope `UNCLASSIFIED`

| Colonna | Origine | Trasformazione / fallback |
|---|---|---|
| `signal_id` | come sopra | Derivato da refs / root. |
| `target_refs_count` | come sopra | Conteggio. |
| `links_count` | come sopra | Conteggio. |
| `hashtags_count` | come sopra | Conteggio. |
| `validation_warning_count` | `normalized.validation_warnings` | Conteggio. |
| `diagnostics_summary` | `normalized.diagnostics` | Sintesi selettiva. |
| `notes_summary` | `normalized.notes` -> `normalized.parsing_notes` | Join. |

## Colonne Debug Opzionali

Queste colonne compaiono solo se abilitate da CLI.

| Colonna | Origine | Trasformazione / fallback |
|---|---|---|
| `legacy_actions` | `normalized.actions` | Join testuale della lista legacy. |
| `normalized_json_debug` | `normalized` completo | JSON dump della struttura normalizzata. |

## Note Tecniche Importanti

- `entries_summary` non usa sempre la stessa sorgente: prima prova `entities.entry` come range, poi `actions_structured.entries`, poi `entry_plan.entries`, poi `normalized.entries`, poi `entities.entry_plan_entries`.
- `reported_results` preferisce l'envelope se presente, poi il `canonical`, poi il payload normalizzato legacy.
- `target_refs` viene unificato da piu campi per evitare duplicati o differenze di origine.
- La vista report puo quindi mostrare campi derivati da envelope/canonical anche quando il JSON nel DB contiene ancora dati legacy.
