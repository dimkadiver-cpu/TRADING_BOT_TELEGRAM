# Promemoria - gap aperto su rischio teorico vs eseguibilita' exchange

Data: 2026-05-25

## Contesto

Caso osservato su `BTCUSDT LONG` con struttura `TWO_STEP`:

- `leg1` viene creata e inviata correttamente;
- `leg2` viene creata dal lifecycle ma fallisce in `ops_execution_commands` con `reason=invalid_order`;
- il problema non e' nel parser e non e' nel planner della chain: il problema nasce nel sizing della seconda leg rispetto ai vincoli reali exchange.

## Punto debole emerso

Il sistema attuale sa calcolare il rischio "sulla carta", ma non garantisce che ogni leg calcolata sia davvero tradabile su exchange.

In pratica:

- il risk engine divide il rischio totale sulle leg usando i pesi configurati;
- per ogni leg calcola `qty = risk_budget_leg / abs(entry_price_leg - stop_loss)`;
- una leg lontana dallo stop puo' diventare molto piccola;
- se quella qty scende sotto i vincoli reali exchange, il bot se ne accorge solo dopo il submit, quando Bybit risponde `invalid_order`.

## Esempio reale visto

Chain BTC osservata:

- rischio totale = `10 USDT`
- pesi `TWO_STEP LIMIT averaging` = `E1 0.7`, `E2 0.3`
- `leg1 risk_budget = 7`
- `leg2 risk_budget = 3`
- `leg2 price = 69663.94`
- `SL = 75276.43`

Risultato:

- `leg2 qty = 0.0005345221 BTC`
- notional circa `37.24 USDT`
- exchange response: `invalid_order`

## Incongruenze attuali

1. Il bot costruisce leg valide per il proprio modello interno, ma non ancora validate contro:
   - `min_order_size`
   - minimo notional
   - `qty_precision`
   - `price_precision`

2. `planned_entry_qty` e somma reale delle qty delle leg non coincidono sempre.
   Questo rende piu' fragile:
   - lettura del piano;
   - rischio residuo;
   - replan;
   - coerenza tra chain e ordini reali.

3. Il runtime live continua a usare `StaticExchangeDataPort()` nel lifecycle.
   Quindi il layer rischio non ha ancora market metadata reali al momento della decisione.

4. I campi account-level:
   - `max_capital_at_risk_pct`
   - `hard_max_per_signal_risk_pct`
   risultano presenti nel config account, ma non sembrano guidare il blocco nel `RiskCapacityEngine`.

## Layer coinvolti

- `src/runtime_v2/signal_enrichment/config_loader.py`
- `src/runtime_v2/signal_enrichment/processor.py`
- `src/runtime_v2/lifecycle/risk_capacity.py`
- `src/runtime_v2/lifecycle/entry_gate.py`
- `src/runtime_v2/execution_gateway/gateway.py`
- `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py`
- `main.py`

## Cosa va chiarito

1. Il modello corretto e' davvero "risk budget per leg" oppure "notional split con vincoli di rischio globali"?

2. Quando una leg calcolata scende sotto il minimo exchange, il comportamento atteso deve essere:
   - `BLOCK`;
   - `REVIEW_REQUIRED`;
   - cancellazione della sola leg con warning;
   - merge della size sulla leg primaria;
   - altro comportamento esplicito.

3. `planned_entry_qty` deve rappresentare:
   - il totale teorico basato sulla prima leg;
   - oppure la somma reale delle qty effettive delle leg pianificate.

## Prossimo passo consigliato

1. Aggiungere validazione owner-layer pre-submit su:
   - `min_order_size`
   - notional minimo
   - precisioni qty/prezzo

2. Decidere e fissare in test la semantica corretta del sizing multi-leg.

3. Allineare `planned_entry_qty` con la somma effettiva delle leg o rinominare il campo se rappresenta altro.

4. Sostituire il `StaticExchangeDataPort()` live con un provider che esponga almeno i metadati minimi di mercato necessari al risk gate.
