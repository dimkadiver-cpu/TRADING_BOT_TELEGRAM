# Fase 9 - Agent Prompts

Prompts per agente da eseguire in sequenza.
Ogni prompt e autonomo e contiene il contesto necessario.
Leggi `docs/PRD_FASE_9.md` prima di iniziare qualsiasi step.

---

## Step 28 - Normalizer: entry legs runtime

```
Leggi questi file prima di scrivere codice:
- docs/PRD_FASE_9.md (Step 28)
- src/execution/freqtrade_normalizer.py
- src/execution/tests/test_freqtrade_bridge.py

Obiettivo: introdurre il modello runtime delle entry legs senza rompere il comportamento esistente.

Cosa fare:

1. Aggiungi il dataclass:
   - FreqtradeEntryLeg(entry_id, sequence, order_type, price, split, role=None)

2. Estendi FreqtradeSignalContext con:
   - entry_legs: tuple[FreqtradeEntryLeg, ...]
   - first_entry_leg
   - market_entry_required
   - limit_entry_required

3. Costruisci entry_legs a partire da:
   - signals.entry_json
   - operational_signals.entry_split_json

4. Regole di split:
   - se entry_split_json esiste, mappa E1/E2/E3 ai pesi
   - se manca:
     - una leg -> 1.0
     - piu legs -> split uguale

5. Mantieni backward compatibility:
   - first_entry_price continua a funzionare
   - first_entry_order_type continua a funzionare ma deriva da first_entry_leg

6. Aggiungi test:
   - single market -> una leg MARKET split 1.0
   - single limit -> una leg LIMIT split 1.0
   - mixed market+limit -> due legs con type corretti
   - entry_split_json -> split applicato correttamente

Vincoli:
- non cambiare il contratto del parser
- non modificare il router
- mantieni i test esistenti verdi
```

---

## Step 29 - Strategy path: LIMIT only

```
Leggi questi file prima di scrivere codice:
- docs/PRD_FASE_9.md (Step 29)
- freqtrade/user_data/strategies/SignalBridgeStrategy.py
- src/execution/tests/test_freqtrade_bridge.py

Obiettivo: fare in modo che la strategy standard gestisca solo segnali con primo leg LIMIT.

Cosa fare:

1. Aggiorna populate_entry_trend():
   - se context.first_entry_order_type != "LIMIT", non emettere enter_long/enter_short

2. Mantieni il guard in confirm_trade_entry():
   - se il segnale richiede MARKET ma runtime order_type e LIMIT -> return False
   - persisti evento ENTRY_ORDER_TYPE_MISMATCH

3. Non cambiare il comportamento dei segnali LIMIT

4. Aggiungi o aggiorna test:
   - LIMIT -> enter_long/enter_short continua a essere emesso
   - MARKET -> populate_entry_trend non emette entry signal
   - MARKET+runtime LIMIT -> reject con evento ENTRY_ORDER_TYPE_MISMATCH

Vincoli:
- non introdurre network call
- non cambiare custom_entry_price() per LIMIT
- non toccare i callback di exit
```

---

## Step 30 - Market Entry Dispatcher

```
Leggi questi file prima di scrivere codice:
- docs/PRD_FASE_9.md (Step 30)
- src/execution/freqtrade_normalizer.py
- src/execution/freqtrade_callback.py
- src/execution/exchange_gateway.py
- src/execution/tests/test_exchange_order_manager.py

Obiettivo: creare un dispatcher che esegue il primo leg MARKET leggendo i segnali pending dal DB.

Cosa fare:

1. Crea nuovo file:
   - src/execution/market_entry_dispatcher.py

2. Implementa classe:
   - MarketEntryDispatcher(db_path, gateway=None)
   - metodo dispatch_pending_market_entries()

3. Seleziona come candidati solo segnali:
   - status=PENDING
   - first_entry_order_type=MARKET
   - is_executable=True
   - nessun trade esistente per attempt_key
   - nessun ordine ENTRY attivo per attempt_key

4. Per ogni candidato:
   - determina pair, side, stake_amount, leverage
   - crea l'entry market
   - se la simulazione/backend restituisce fill immediato, chiama order_filled_callback()
   - registra evento MARKET_ENTRY_DISPATCHED

5. Restituisci una lista risultati con:
   - attempt_key
   - ok
   - action
   - error eventuale

6. Aggiungi test nuovo file:
   - test dispatch single market
   - test idempotenza
   - test skip se trade gia esistente
   - test skip se first leg non e MARKET

Vincoli:
- non fare dipendere il dispatcher da FreqtradeBot interno
- usa i layer execution gia esistenti
- preferisci backend/gateway gia presenti
```

---

## Step 31 - Idempotenza e audit trail

```
Leggi questi file prima di scrivere codice:
- docs/PRD_FASE_9.md (Step 31)
- src/execution/market_entry_dispatcher.py
- src/execution/freqtrade_callback.py
- src/execution/freqtrade_normalizer.py

Obiettivo: rendere il dispatch market sicuro e ripetibile senza doppie entry.

Cosa fare:

1. Aggiungi controllo idempotente prima del dispatch:
   - se esiste trade per attempt_key -> skip
   - se esiste ordine ENTRY attivo -> skip
   - se esiste evento MARKET_ENTRY_DISPATCHED per entry_id -> skip

2. Aggiungi helper per eventi di dispatch:
   - MARKET_ENTRY_DISPATCHED
   - MARKET_ENTRY_DISPATCH_FAILED

3. Se utile, salva in trades.meta_json:
   - entry_legs
   - stato della leg E1 dopo il fill

4. Aggiungi test:
   - doppio pass del dispatcher -> seconda esecuzione no-op
   - errore backend -> event failed
   - fill riuscito -> event dispatched + entry_filled

Vincoli:
- non duplicare logica gia presente in order_filled_callback()
- audit trail prima, ottimizzazioni dopo
```

---

## Step 32 - Mixed entry plan readiness

```
Leggi questi file prima di scrivere codice:
- docs/PRD_FASE_9.md (Step 32)
- src/execution/freqtrade_normalizer.py
- src/execution/freqtrade_callback.py
- freqtrade/user_data/strategies/SignalBridgeStrategy.py

Obiettivo: preparare il runtime ai segnali misti E1 MARKET + E2/E3 LIMIT senza dover completare tutto l'averaging live nello stesso step.

Cosa fare:

1. Quando E1 MARKET viene fillato:
   - salva nel trade.meta_json lo stato delle entry legs
   - E1 -> FILLED
   - E2/E3 -> PENDING

2. Non piazzare ancora automaticamente E2/E3 come ordini exchange se la logica non e pronta
   - basta conservare il piano runtime in modo leggibile e testato

3. Esporre helper per leggere:
   - next pending entry leg
   - pending limit legs residue

4. Aggiungi test:
   - mixed plan salva correttamente E1 FILLED e E2 pending
   - plan serializzato in meta_json
   - nessuna regressione su single LIMIT e single MARKET

Vincoli:
- non implementare tutto l'averaging execution se richiede troppo codice non testato
- preparare un handoff pulito per fase successiva
```

---

## Step finale - Verifica end-to-end

```
Leggi questi file prima di eseguire:
- docs/PRD_FASE_9.md
- docs/FREQTRADE_RUNBOOK.md
- freqtrade/user_data/config.json

Obiettivo: verificare il comportamento live/dry-run della nuova Fase 9.

Cosa fare:

1. Esegui test mirati:
   - pytest su test_freqtrade_bridge.py
   - pytest su eventuali test dispatcher nuovi

2. Verifica scenari:
   - single LIMIT
   - single MARKET
   - mixed MARKET + LIMIT

3. Conferma nel DB:
   - single MARKET non crea ordine limit pendente
   - event ENTRY_ORDER_TYPE_MISMATCH appare solo quando necessario
   - mixed plan salva entry_legs in trade.meta_json

4. Aggiorna docs/GAP_ANALYSIS.md se la fase chiude o riduce gap attivi

Vincoli:
- se riavvii freqtrade, documenta il comando usato
- non dichiarare chiuso cio che non e stato verificato
```

