# Promemoria Bug 1 — REBUILD_PARTIAL_TPS sotto minimo exchange

## Contesto

Nel DB live `db\Test_live\db\ops.sqlite3` il cluster principale dei `GATEWAY: COMMAND FAILED` riguarda `REBUILD_PARTIAL_TPS` su `BTCUSDT`.

Pattern osservato:

- fill entry piccoli
- molti target intermedi
- qty TP ricostruita sotto il minimo reale Bybit demo
- reject exchange: `The number of contracts exceeds minimum limit allowed`

## Root cause

Il problema owner-layer sta in `PostFillProtectionRebuilder`:

- file: `src/runtime_v2/lifecycle/post_fill_rebuilder.py`
- punto critico: `tp_qty = round(filled_entry_qty * (100.0 / n_total_tps) / 100.0, 8)`
- subito dopo il comando `REBUILD_PARTIAL_TPS` viene emesso senza validazione preventiva su `min_order_size`

Quindi il lifecycle crea payload tecnicamente non piazzabili e lascia che il failure emerga solo nel gateway.

## Secondo problema collegato

Nel DB gli snapshot mercato per `BTCUSDT` demo riportano `min_order_size = 1e-06`, ma i failure reali mostrano che il minimo effettivo per il caso osservato e` `0.001`.

Questo indica che il dato locale oggi non e` affidabile per proteggere il rebuild dei TP parziali.

## Decisione tecnica

Il fix corretto va separato in due livelli:

1. **Guardia nel lifecycle**
   - prima di emettere `REBUILD_PARTIAL_TPS`, validare `tp_qty >= min_order_size`
   - se il TP parziale non e` piazzabile, non emettere il comando
   - preferire un evento esplicito non-failing invece di generare un `COMMAND FAILED`

2. **Correzione della sorgente del minimo mercato**
   - chiarire la differenza tra `qtyStep/basePrecision` e vero `minOrderQty`
   - evitare che `BTCUSDT` demo venga trattato con minimo `1e-06` quando il comportamento exchange reale smentisce il dato

## Raccomandazione prodotto

Comportamento minimo e sicuro:

- se il rebuild genera TP intermedi sotto minimo exchange, saltare il rebuild parziale
- lasciare attivi SL e full TP gia` coerenti col piano
- non degradare il caso in errore gateway

## Nota

Questo bug non va fixato nel control plane e non va silenziato nel gateway.
Va fermato nel lifecycle, prima dell'emissione del comando.
