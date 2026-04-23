# Layer 4 — estratto parser, nomi canonici e passaggio al layer successivo

## Scopo

Questo documento riassume il **Layer 4 (Parsing / normalization / parse_results)** del repo `BACK_TESTING`, limitatamente a ciò che è **verificabile dal codice presente nel repository**.

Nota importante:
- in questo workspace è verificabile soprattutto il flusso **offline / replay** sotto `parser_test`;
- i moduli live `src.telegram` non fanno parte del workspace corrente;
- quindi questo documento descrive il comportamento reale del **replay parser + replay operation rules**, non l'intero runtime live originale.

---

## Flusso sintetico

Il passaggio reale osservabile nel repo è questo:

```text
raw_messages
  -> replay_parser.py
  -> parser profilo-specifico
  -> parse_results
       - campi indicizzati separati
       - parse_result_normalized_json
  -> replay_operation_rules.py
  -> signals + operational_signals
  -> chain_builder / backtesting layer
```

In pratica:
1. il parser legge `raw_messages`;
2. risolve il trader (`resolved_trader_id`);
3. classifica il messaggio (`NEW_SIGNAL`, `UPDATE`, ecc.);
4. produce un payload normalizzato;
5. salva il risultato in `parse_results`;
6. il layer successivo rilegge `parse_results` e materializza `signals` e `operational_signals`.

---

## File principali verificati

### Replay / Layer 4
- `parser_test/scripts/replay_parser.py`
- `src/signal_chain_lab/parser/trader_profiles/base.py`
- `src/signal_chain_lab/storage/parse_results.py`

### Profili parser verificati
- `src/signal_chain_lab/parser/trader_profiles/trader_a/profile.py`
- `src/signal_chain_lab/parser/trader_profiles/trader_b/profile.py`
- `src/signal_chain_lab/parser/trader_profiles/registry.py`

### Layer successivo
- `parser_test/scripts/replay_operation_rules.py`
- `src/signal_chain_lab/storage/operational_signals_store.py`
- `src/signal_chain_lab/storage/signals_store.py`

### Normalizzazione canonica
- `src/signal_chain_lab/engine/state_machine.py`
- `src/signal_chain_lab/parser/intent_action_map.py`
- `src/signal_chain_lab/parser/canonical_schema.py`

### Documentazione repo utile
- `docs/data-contracts.md`
- `README.md`

---

## 1. Contratto logico del parser (framework)

Il contratto comune del parser è `TraderParseResult`.

Campi previsti dal framework:

- `message_type`
- `intents`
- `entities`
- `target_refs`
- `reported_results`
- `warnings`
- `confidence`
- `primary_intent`
- `actions_structured`
- `target_scope`
- `linking`
- `diagnostics`

Quindi il Layer 4, a livello logico, è più ricco del semplice schema `message_type + intents + entities`.

---

## 2. `message_type` previsti

Tipi messaggio osservati nel codice:

- `NEW_SIGNAL`
- `UPDATE`
- `INFO_ONLY`
- `SETUP_INCOMPLETE`
- `UNCLASSIFIED`

Nel DB `parse_results` vengono poi salvati anche metadati aggiuntivi di stato parser:

- `parse_status` = `PARSED` / `SKIPPED`
- `completeness` = `COMPLETE` / `INCOMPLETE`
- `is_executable` = boolean

Regola attuale importante:
- `is_executable=True` solo quando `message_type == NEW_SIGNAL` e `completeness == COMPLETE`.

---

## 3. Intent canonici previsti dal framework

Dal mapping framework e dagli helper canonici risultano questi intent principali:

- `NS_CREATE_SIGNAL`
- `U_MOVE_STOP`
- `U_MOVE_STOP_TO_BE`
- `U_CANCEL_PENDING_ORDERS`
- `U_REMOVE_PENDING_ENTRY`
- `U_CLOSE_PARTIAL`
- `U_CLOSE_FULL`
- `U_REENTER`
- `U_INVALIDATE_SETUP`
- `U_MARK_FILLED`
- `U_TP_HIT`
- `U_STOP_HIT`
- `U_REPORT_FINAL_RESULT`
- `U_MANUAL_CLOSE`
- `U_ADD_ENTRY`
- `U_ACTIVATION`
- `U_UPDATE_PENDING_ENTRY`
- `U_UPDATE_TAKE_PROFITS`
- `U_EXIT_BE`
- `U_REVERSE_SIGNAL`
- `U_RISK_NOTE`

### Nota importante
Non tutti i trader supportano tutti gli intent.

Il file `canonical_schema.py` mantiene anche una matrice di supporto per trader (`TA`, `TB`, `TC`, `T3`, `TD`).

---

## 4. Nomi canonici normalizzati dal framework

Il framework applica alcune normalizzazioni esplicite di alias.

### Alias -> canonico

- `side` -> `direction`
- `new_stop_level` -> `new_sl_level`
- `new_stop_price` -> `new_sl_price`
- `new_stop_reference_text` -> `new_sl_reference`
- `partial_close_percent` -> `close_pct`

### Semantica entry canonica

Il framework normalizza anche la semantica delle entry verso:

- `entry_type` = `MARKET` / `LIMIT`
- `entry_structure` =
  - `ONE_SHOT`
  - `TWO_STEP`
  - `RANGE`
  - `LADDER`

Valori legacy che vengono convertiti:
- `SINGLE` -> `ONE_SHOT`
- `ZONE` -> `LIMIT + RANGE`
- `AVERAGING` -> `LIMIT + TWO_STEP` oppure `LIMIT + LADDER`
- `entry_plan_type` legacy -> coppia canonica `entry_type + entry_structure`

---

## 5. Campi top-level che il parser può produrre

Questi sono i campi top-level logici del parser:

| Campo | Significato |
|---|---|
| `message_type` | Classe del messaggio |
| `intents` | Lista intent rilevati |
| `entities` | Entità estratte dal testo |
| `target_refs` | Riferimenti target espliciti |
| `reported_results` | Risultati riportati dal trader |
| `warnings` | Warning di parsing |
| `confidence` | Confidenza parser |
| `primary_intent` | Intent principale sintetico |
| `actions_structured` | Azioni v2 strutturate |
| `target_scope` | Scope logico del target |
| `linking` | Metadati di linking |
| `diagnostics` | Diagnostica parser |

---

## 6. Entità osservate nei profili parser

Qui sotto non c'è il "desiderato teorico", ma i campi realmente osservati nei profili verificati (`trader_a`, `trader_b`).

### 6.1 Entità tipiche di `NEW_SIGNAL` / `SETUP_INCOMPLETE`

Campi osservati:

- `symbol`
- `direction`
- `entry`
- `stop_loss`
- `take_profits`
- `averaging`
- `entry_plan_entries`
- `entry_type`
- `entry_plan_type`
- `entry_structure`
- `has_averaging_plan`
- `entry_order_type`
- `risk_percent`
- `potential_profit_percent`
- `market_context`
- `setup_invalidation`

### 6.2 Entità tipiche di `UPDATE`

Campi osservati:

- `new_stop_level`
- `new_stop_price`
- `new_stop_reference_text`
- `stop_reference_text`
- `close_scope`
- `close_fraction`
- `hit_target`
- `fill_state`
- `cancel_scope`
- `result_percent`
- `result_mode`
- `update_tense`
- `setup_invalidation`

### 6.3 Risultati riportati (`reported_results`)

Quando presenti, gli item osservati hanno tipicamente:

- `symbol`
- `value`
- `unit`

Esempio concettuale:

```json
{
  "symbol": "BTCUSDT",
  "value": 1.2,
  "unit": "R"
}
```

---

## 7. `target_refs`, `target_scope`, `linking`

### 7.1 `target_refs`

Riferimenti target osservati:

- `reply`
- `telegram_link`
- `message_id`
- in alcuni consumatori anche `signal_id` come riferimento logico

### 7.2 `target_scope`

Il parser può produrre scope tipo:

- `signal/self`
- `signal/single`
- `signal/unknown`
- `portfolio_side`

Valori di scope osservati:

- `ALL_LONGS`
- `ALL_SHORTS`
- `ALL_ALL`
- `ALL_OPEN`
- `ALL_REMAINING`
- `ALL_REMAINING_SHORTS`
- `ALL_REMAINING_LONGS`

### 7.3 `linking`

Metadati di linking osservati:

- `targeted`
- `reply_to_message_id`
- `target_refs_count`
- `has_global_target_scope`
- `strategy`
- in alcuni profili anche `telegram_link_count`

Strategie osservate:
- `reply_or_link`
- `global_scope`
- `unresolved`

---

## 8. `actions_structured` — azioni canoniche lato parser

Nel parser v2 sono state osservate azioni come:

- `CREATE_SIGNAL`
- `MOVE_STOP`
- `CLOSE_POSITION`
- `CANCEL_PENDING`
- `TAKE_PROFIT`
- `MARK_FILLED`
- `REPORT_RESULT`

Con payload associato, ad esempio:

- `new_stop_level`
- `scope`
- `close_fraction`
- `target`
- `fill_state`
- `mode`
- `targeting`

### Modalità di targeting osservate

- `EXPLICIT_TARGETS`
- `TARGET_GROUP`
- `SELECTOR`

Questo significa che il parser può già produrre una semantica abbastanza vicina al livello operativo.

---

## 9. Cosa salva davvero oggi `replay_parser.py` in `parse_results`

Questo è il punto più importante dell'audit.

Il parser **può produrre** molti campi top-level, ma il replay attuale serializza in `parse_result_normalized_json` solo:

- `message_type`
- `intents`
- `entities`
- `target_refs`
- `actions_structured`
- `warnings`
- `confidence`

Quindi oggi **non risultano persistiti nel JSON salvato**:

- `reported_results`
- `primary_intent`
- `target_scope`
- `linking`
- `diagnostics`

### Conseguenza pratica

Il contratto logico del parser è più ricco del payload realmente salvato in DB.

In altre parole:
- il parser calcola più informazione;
- ma il layer successivo riceve solo una parte di quella informazione attraverso `parse_result_normalized_json`.

---

## 10. Campi indicizzati separati salvati in `parse_results`

Oltre al JSON normalizzato, `parse_results` salva campi separati e indicizzabili:

- `raw_message_id`
- `eligibility_status`
- `eligibility_reason`
- `declared_trader_tag`
- `resolved_trader_id`
- `trader_resolution_method`
- `message_type`
- `parse_status`
- `completeness`
- `is_executable`
- `symbol`
- `direction`
- `entry_raw`
- `stop_raw`
- `target_raw_list`
- `leverage_hint`
- `risk_hint`
- `risky_flag`
- `linkage_method`
- `linkage_status`
- `warning_text`
- `notes`
- `parse_result_normalized_json`
- `created_at`
- `updated_at`

### Nota
Di questi, nel replay attuale i più usati dal layer successivo sono soprattutto:

- `resolved_trader_id`
- `message_type`
- `completeness`
- `is_executable`
- `symbol`
- `direction`
- `parse_result_normalized_json`

---

## 11. Come il Layer 4 passa al layer successivo

Il layer successivo, in questo repo, è `replay_operation_rules.py`.

Questo legge da `parse_results`:

- `parse_result_id`
- `raw_message_id`
- `source_chat_id`
- `telegram_message_id`
- `reply_to_message_id`
- `raw_text`
- `message_ts`
- `resolved_trader_id`
- `message_type`
- `completeness`
- `is_executable`
- `symbol`
- `direction`
- `parse_result_normalized_json`

Quindi il passaggio reale verso il layer successivo non è tramite oggetti in memoria, ma tramite:

1. riga `parse_results`
2. JSON `parse_result_normalized_json`
3. alcuni campi denormalizzati laterali (`message_type`, `symbol`, `direction`, ecc.)

---

## 12. Cosa fa il layer successivo con l'output parser

### 12.1 Se il messaggio è `NEW_SIGNAL`

Il layer successivo:
- costruisce `attempt_key`
- verifica `completeness`
- se incompleto -> `is_blocked=True`
- se completo -> inserisce in `signals`
- in ogni caso inserisce una riga audit/operativa in `operational_signals`

Per costruire `signals`, estrae dal payload parser:

- `symbol`
- `side/direction`
- `entry_plan_entries` oppure `entries` / `entry`
- `stop_loss`
- `take_profits`
- `confidence`

### 12.2 Se il messaggio è `UPDATE`

Il layer successivo:
- prova a risolvere il target del messaggio;
- se lo risolve, salva `resolved_target_ids` in `operational_signals`;
- se non lo risolve, lo marca come orphan / `UNRESOLVED`.

---

## 13. Strategia di linking osservata verso il layer successivo

Nel replay delle operation rules, la risoluzione target segue questo ordine:

### 13.1 Priorità 1: reply diretto
Se `reply_to_message_id` è presente:
- cerca il `NEW_SIGNAL` corrispondente nello stesso trader/chat;
- se lo trova, collega l'UPDATE a quel segnale.

### 13.2 Priorità 2: `target_refs` di tipo `signal_id`
Se il payload contiene target refs logici di tipo `signal_id`:
- confronta il `signal_id` dell'UPDATE con i `NEW_SIGNAL` già presenti;
- se trova match, collega gli id operativi.

### 13.3 Fallback a livello chain builder
La documentazione del repo (`docs/data-contracts.md`) descrive poi, per la ricostruzione chain:

1. uso di `operational_signals.resolved_target_ids`
2. fallback su `raw_messages.reply_to_message_id`

---

## 14. Output DB del layer successivo

### 14.1 Tabella `signals`
Per i `NEW_SIGNAL` validi, il layer successivo produce una riga `signals` con campi come:

- `attempt_key`
- `env`
- `channel_id`
- `root_telegram_id`
- `trader_id`
- `trader_prefix`
- `symbol`
- `side`
- `entry_json`
- `sl`
- `tp_json`
- `status`
- `confidence`
- `raw_text`
- `created_at`
- `updated_at`

### 14.2 Tabella `operational_signals`
Per `NEW_SIGNAL` e `UPDATE`, produce una riga operativa con campi come:

- `parse_result_id`
- `attempt_key`
- `trader_id`
- `message_type`
- `is_blocked`
- `block_reason`
- `risk_*`
- `entry_split_json`
- `management_rules_json`
- `applied_rules_json`
- `warnings_json`
- `resolved_target_ids`
- `target_eligibility`
- `target_reason`
- `created_at`

---

## 15. Distinzione pratica: cosa è “contratto parser” e cosa arriva davvero avanti

### 15.1 Contratto parser teorico/logico
Il parser framework supporta:

- message typing
- intents canonici
- entities
- targeting
- linking
- diagnostics
- actions_structured
- reported_results
- primary_intent

### 15.2 Payload realmente persistito oggi
Nel replay attuale viene salvato soprattutto:

- `message_type`
- `intents`
- `entities`
- `target_refs`
- `actions_structured`
- `warnings`
- `confidence`

### 15.3 Quindi cosa arriva davvero al layer successivo
In pratica il layer successivo riceve bene:

- tipo messaggio
- completezza
- trader risolto
- symbol / direction
- entities principali
- refs target
- actions structured
- warning / confidence

Ma **non riceve in modo persistito completo**:

- `primary_intent`
- `target_scope`
- `linking`
- `diagnostics`
- `reported_results`

---

## 16. Valutazione sintetica del Layer 4

### Punti forti
- struttura parser abbastanza ricca;
- profili trader separati e specializzati;
- supporto a intent canonici;
- normalizzazione alias e semantica entry;
- `actions_structured` già vicine a una semantica operativa;
- passaggio DB chiaro verso `signals` e `operational_signals`.

### Punto debole principale rilevato
Il parser calcola più informazione di quella che oggi salva realmente in `parse_results.parse_result_normalized_json`.

Questo crea una differenza tra:
- **contratto logico del parser**
- **contratto persistito / realmente consumato dal layer successivo**

### Conclusione pratica
Se vuoi che il Layer 5 lavori davvero con tutta la semantica del Layer 4, il primo punto da migliorare è:

- allargare la serializzazione di `parse_result_normalized_json`

in modo da includere anche:

- `primary_intent`
- `target_scope`
- `linking`
- `diagnostics`
- `reported_results`

---

## 17. Versione ultra-compatta

### Il Layer 4 fa questo
- prende `raw_messages`
- risolve il trader
- classifica il messaggio
- estrae intent ed entità
- salva il risultato in `parse_results`

### Le variabili canoniche principali sono
- `message_type`
- `intents`
- `entities`
- `target_refs`
- `actions_structured`
- `confidence`
- `warnings`

### Le entità canoniche più importanti sono
- setup: `symbol`, `direction`, `entry`, `stop_loss`, `take_profits`, `entry_plan_entries`
- update: `new_stop_level`, `close_scope`, `close_fraction`, `hit_target`, `fill_state`, `cancel_scope`, `result_percent`

### Come passa avanti
- salva in `parse_results`
- il layer successivo legge `parse_result_normalized_json`
- produce `signals` e `operational_signals`

### Problema principale
- il parser calcola più campi di quelli che oggi vengono salvati nel JSON persistito.

---

## 18. Sorgenti controllate per questo documento

- `parser_test/scripts/replay_parser.py`
- `src/signal_chain_lab/parser/trader_profiles/base.py`
- `src/signal_chain_lab/storage/parse_results.py`
- `parser_test/scripts/replay_operation_rules.py`
- `src/signal_chain_lab/storage/operational_signals_store.py`
- `src/signal_chain_lab/storage/signals_store.py`
- `src/signal_chain_lab/engine/state_machine.py`
- `src/signal_chain_lab/parser/intent_action_map.py`
- `src/signal_chain_lab/parser/canonical_schema.py`
- `src/signal_chain_lab/parser/trader_profiles/trader_a/profile.py`
- `src/signal_chain_lab/parser/trader_profiles/trader_b/profile.py`
- `docs/data-contracts.md`
- `README.md`

