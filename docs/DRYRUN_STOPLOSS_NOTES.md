# DRYRUN_STOPLOSS_NOTES

## Contesto

Caso osservato sul trade BTC `T_-1003171748254_3468_trader_c` in ambiente `dry_run` Freqtrade.

Il bot applica correttamente la logica di stop:
- entry fill reale: `66222.6`
- partial TP eseguito
- move stop to breakeven applicato correttamente lato bot
- `signals.sl = 66222.6` nel DB bot

Pero nel DB Freqtrade restano multipli ordini `stoploss` con `status='open'`, sia storici sia recenti.

## Osservazione tecnica

Il problema non sembra stare nel parser o nella logica bot di business.

Il caso BTC e in `strategy_managed`, quindi gli stoploss passano dal lifecycle interno Freqtrade `stoploss_on_exchange`, non dal nostro `ExchangeOrderManager` custom.

Punti osservati:
- `freqtradebot.handle_stoploss_on_exchange()` crea e aggiorna gli stoploss exchange-side.
- `handle_trailing_stoploss_on_exchange()` cancella lo stoploss corrente e ne crea uno nuovo.
- `manage_trade_stoploss_orders()` considera di fatto l'ultimo stoploss attivo, ma non sanifica i vecchi duplicati gia persistiti come `open`.
- In `dry_run`, `fetch_dry_run_order()` puo ricaricare ordini dal DB persistito e reintrodurli nello stato runtime in memoria.

Questo porta a uno stato sporco nel DB/UI:
- SL logico corretto
- multipli record `stoploss` ancora `open`
- possibile confusione operativa in analisi e debug

## Impatto

Ad oggi, sul caso BTC osservato:
- il livello di SL effettivo del bot e corretto
- il problema principale e di lifecycle/persistenza `dry_run`
- il rischio sembra piu alto per audit/UI/debug che per decisione logica del bot

## Decisione attuale

Non correggere subito.

Prima eseguire test su caso reale, senza ulteriori interventi sul runtime, per distinguere:
- limite/bug del `dry_run` Freqtrade
- bug realmente rilevante anche su esecuzione reale

## Prossimi controlli suggeriti

Quando si fara il test reale:
- verificare se gli stoploss duplicati compaiono anche fuori da `dry_run`
- verificare se il cancel/replace exchange-side lascia davvero ordini multipli attivi oppure no
- confrontare DB/UI Freqtrade con stato reale exchange

## Nota architetturale

Se il problema restera limitato a `dry_run`, la correzione potrebbe essere trattata come:
- sanificazione locale di persistenza per test/debug
oppure
- workaround documentato

ma non necessariamente come bug critico del flusso live.
