# Gap Analysis — Pipeline Parser → Esecuzione

Data analisi: 2026-03-28

Documento che raccoglie le feature incomplete o mancanti nel flusso end-to-end:
Parser → Validation → Operation Rules → Freqtrade → Exchange.

---

## Gap critici (bloccanti per production)

### GAP-01 — Target Resolver: eligibility incompleta ✅ CHIUSO (parziale)

**Dove:** `src/target_resolver/resolver.py`, `src/telegram/router.py`
**Stato:** ~~Interface presente, logica di validazione scarsa~~ → **RISOLTO** (2026-03-28)
**Analisi:**
- La logica di eligibility (stato posizione, symbol match, trader match) era già presente e funzionante
- Il conflict detection su target multipli è stato valutato e ritenuto non necessario: se un UPDATE SYMBOL colpisce 2 trade, va bene che si applichi a entrambi
- Il problema reale era solo: `UNRESOLVED` non veniva gestito a valle

**Fix applicato:**
- `router.py`: dopo Step 3 (resolve), se `eligibility == "UNRESOLVED"` e `message_type == "UPDATE"` → inserimento in `review_queue` con reason `update_target_unresolved:{reason}` + warning log
- Il record in `operational_signals` viene comunque persistito per audit trail
- Test aggiunti in `TestPhase4UnresolvedUpdateReviewQueue` (2 casi: unresolved → review, resolved → no review)

**Impatto residuo:** Nessuno.

---

### GAP-02 — EntryPricePolicy non collegata alla strategy freqtrade ✅ CHIUSO

**Dove:** `freqtrade/user_data/strategies/SignalBridgeStrategy.py` (linee 517-548)
**Stato:** ~~Implementato ma non chiamato~~ → **RISOLTO** (2026-03-28)
**Verifica:**
- `check_entry_rate` è chiamato dentro `confirm_trade_entry()` con policy risolta da `resolve_entry_price_policy()`
- In caso di rifiuto: log warning + evento `ENTRY_PRICE_REJECTED` persistito nel DB + `return False` che blocca l'ordine
- Il GAP era stato scritto prima che `SignalBridgeStrategy.py` venisse implementato (file untracked in git al momento dell'analisi)

**Impatto residuo:** Nessuno.

---

### GAP-03 — Watchdog ordini orfani assente ✅ CHIUSO

**Dove:** `src/execution/order_reconciliation.py`, `src/execution/freqtrade_exchange_backend.py`, `freqtrade/user_data/strategies/SignalBridgeStrategy.py`
**Stato:** ~~Riconciliazione solo al bootstrap~~ → **RISOLTO** (2026-03-28)
**Analisi:**
- Il framework watchdog esisteva già in `_maybe_run_execution_reconciliation()` ma era disabilitato
- Causa 1: `exchange_order_manager` non veniva mai inizializzato → `getattr(self, "exchange_order_manager", None)` restituiva sempre `None`
- Causa 2: `reconciliation_watchdog_interval_s` mancante in config → ritornava `0.0` → branch periodico mai eseguito

**Fix applicato:**
- Creato `FreqtradeExchangeBackend` (`src/execution/freqtrade_exchange_backend.py`): adapter che wrappa l'exchange freqtrade e implementa il protocollo `ExchangeGatewayBackend`
- Aggiunto hook `bot_start()` in `SignalBridgeStrategy`: inizializza `exchange_order_manager` con il backend al momento in cui freqtrade avvia il bot
- Aggiunto `reconciliation_watchdog_interval_s: 60` in `config.json` e `config.template.json`
- Test: 19 casi in `test_freqtrade_exchange_backend.py` (field mapping, error handling, integrazione con gateway)

**Flusso abilitato:**
```
bot_start() → FreqtradeExchangeBackend(dp._exchange) → ExchangeGateway → ExchangeOrderManager
populate_indicators() ogni candela → _maybe_run_execution_reconciliation()
  [prima candela]  bootstrap_sync_open_trades(reason="startup")
  [ogni 60s]       bootstrap_sync_open_trades(reason="watchdog")
```

**Impatto residuo:** Nessuno.

---

## Gap medi (funzionalità parziali)

### GAP-04 — machine_event rules non eseguite ✅ CHIUSO

**Dove:** `src/execution/machine_event.py` (NUOVO), `src/execution/freqtrade_callback.py`
**Stato:** ~~Configurazione accettata e persistita, non eseguita~~ → **RISOLTO** (2026-03-28)
**Analisi:**
- Le regole `machine_event.rules` erano salvate nel snapshot ma ignorate a runtime (`MACHINE_EVENT_RULES_NOT_SUPPORTED = True`)
- Scope implementato: `TP_EXECUTED → MOVE_STOP_TO_BE` e `EXIT_BE → MARK_EXIT_BE` (le due regole configurate in `operation_rules.yaml`)

**Fix applicato:**
- Creato `machine_event.py`: rule engine puro — `evaluate_rules(event_type, event_context, management_rules)` → `list[MachineEventAction]`
- Hook in `partial_exit_callback`: dopo ogni TP fill, chiama `_fire_tp_machine_events()` → se tp_level corrisponde, aggiorna `signals.sl = entry_price`, setta `be_stop_active = True` in `trades.meta_json`, logga `MACHINE_EVENT_MOVE_STOP_TO_BE`
- Hook in `stoploss_callback`: dopo ogni stop hit, chiama `_fire_sl_machine_events()` → se `be_stop_active == True`, valuta regole `EXIT_BE`, esegue `MARK_EXIT_BE` (flag `breakeven_exit = True` in meta_json), logga `MACHINE_EVENT_EXIT_BE`
- Test: 18 casi in `test_machine_event.py` (unit + integrazione)

**Catena completa:**
```
TP2 fill → partial_exit_callback → MACHINE_EVENT_MOVE_STOP_TO_BE → signals.sl = entry_price
(prezzo torna) SL hit → stoploss_callback → be_stop_active=True → MACHINE_EVENT_EXIT_BE → breakeven_exit=True
```

**Impatto residuo:** Nessuno. `MOVE_STOP_TO_BE` per `exchange_manager` (cancel/replace SL sull'exchange) da implementare con GAP-05.

---

### GAP-05 — Update Applier frammentato

**Dove:** `src/execution/` (nessun file `update_applier.py` unificato)
**Stato:** Gestione degli intent UPDATE distribuita tra più moduli
**Problema:**
- `U_MOVE_STOP` → gestito da `ExchangeOrderManager`
- `U_CLOSE_FULL` → richiede chiamata manuale a freqtrade sell
- `U_CANCEL_PENDING` → richiede cancellazione ordine via gateway
- `U_CLOSE_PARTIAL`, `U_ADD_ENTRY`, `U_MODIFY_ENTRY` → non hanno un handler definito

**Impatto:** Gli intent UPDATE dal parser vengono classificati correttamente ma non tutti producono azioni sull'exchange.

---

### GAP-06 — price_corrections hook non implementato

**Dove:** `src/execution/freqtrade_normalizer.py` (linee 238-245)
**Stato:** Dichiarato, persiste la config, mai eseguito
**Problema:**
- La config `price_corrections` viene salvata in `operational_signals.price_corrections_json`
- Non viene mai applicata durante l'execution (nessun codice legge e usa quel campo)

**Impatto:** Basso — esiste la policy alternativa `EntryPricePolicy`. Ma crea aspettativa falsa nella config.

---

## Gap bassi (feature non prioritarie)

### GAP-07 — live_equity capital sizing non implementato

**Dove:** `src/operation_rules/loader.py` (linea 209, `capital_base_mode`)
**Stato:** Config option `live_equity` presente, non implementata
**Problema:**
- Solo la modalità `static_config` è funzionante — usa `capital_base_usdt` dalla config YAML
- La modalità `live_equity` richiederebbe una fetch real-time del balance da freqtrade o dall'exchange
- Nessuna integrazione con freqtrade wallet API o balance endpoint

**Impatto:** Il sizing della posizione non si adatta al capitale reale disponibile — usa sempre il valore statico configurato.

---

### GAP-08 — Transizioni di stato signal non centralizzate

**Dove:** `src/execution/freqtrade_callback.py` + `src/telegram/router.py`
**Stato:** Aggiornamenti di stato sparsi nei callback
**Problema:**
- Lo stato del signal (`PENDING → ACTIVE → CLOSED`) viene aggiornato in più punti senza una state machine esplicita
- Non esiste validazione che impedisca transizioni illegali (es. `CLOSED → ACTIVE`)

**Impatto:** Basso in operatività normale. Problematico per debugging e audit trail.

---

## Coverage test mancante

| Area | File test esistente | Gap |
|---|---|---|
| EntryPricePolicy enforcement | Nessuno | Validazione prezzo non testata |
| UPDATE directive targeting | Nessuno | Eligibility resolver non testato |
| Operation rules cascading | Parziale | Compound caps tra trader non testati |
| machine_event rule application | Nessuno | Non implementato → non testabile |
| Watchdog reconciliation | Nessuno | Scenario crash-recovery non testato |
| Update intent handlers | Nessuno | U_CLOSE_PARTIAL, U_ADD_ENTRY non testati |

---

## Cosa funziona end-to-end oggi

| Flusso | Stato |
|---|---|
| Parse → Validate → Op.Rules → `signals` DB | Funzionante |
| `order_filled_callback` → trade + orders DB | Funzionante |
| `protective_orders_mode = exchange_manager` → SL + TP su Bybit | Funzionante |
| Bootstrap reconciliation al riavvio | Funzionante |
| Dynamic pairlist per freqtrade | Funzionante |
| UPDATE semplici con target STRONG/REPLY | Parziale (risoluzione OK, applicazione incompleta) |
