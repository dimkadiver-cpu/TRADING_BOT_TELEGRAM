Ecco la mappa completa di tutti i worker che girano sull'event loop — questo è il
  "processo intero" da rivedere prima di mettere mano.

  Tutti i worker sul loop (per account/adapter)

  Worker / coroutine: lifecycle loop → gate_worker.run_once
  Frequenza: event-driven (fallback 10s)
  Cosa fa: gate entries
  ccxt bloccante?: No (solo DB)
  ────────────────────────────────────────
  Worker / coroutine: → timeout_worker.run_once
  Frequenza: idem
  Cosa fa: timeout entry scaduti
  ccxt bloccante?: No (DB)
  ────────────────────────────────────────
  Worker / coroutine: → lifecycle_event_worker.run_once
  Frequenza: idem
  Cosa fa: proietta eventi lifecycle
  ccxt bloccante?: No (DB)
  ────────────────────────────────────────
  Worker / coroutine: → execution_worker.run_once (command)
  Frequenza: idem
  Cosa fa: piazza ordini via gateway
  ccxt bloccante?: Sì (× N comandi)
  ────────────────────────────────────────
  Worker / coroutine: _run_sync_worker → run_once() = run_reconciliation
  Frequenza: ogni 8s
  Cosa fa: loop su ordini SENT/ACK → get_order_status
  ccxt bloccante?: Sì × N ordini
  ────────────────────────────────────────
  Worker / coroutine: _run_reconciliation_periodically → run_reconciliation
  Frequenza: 60s (poll_fallback)
  Cosa fa: stesso di sopra
  ccxt bloccante?: Sì × N ordini
  ────────────────────────────────────────
  Worker / coroutine: _run_position_reconciliation_periodically (3 metodi in fila)
  Frequenza: ~600s
  Cosa fa: vedi sotto
  ccxt bloccante?: Sì
  ────────────────────────────────────────
  Worker / coroutine: → run_position_reconciliation
  Frequenza:
  Cosa fa: loop su chain aperte → get_position_qty_with_details (+
    fetch_recent_reduce_trades)
  ccxt bloccante?: Sì × N chain
  ────────────────────────────────────────
  Worker / coroutine: → run_trade_based_reconciliation
  Frequenza:
  Cosa fa: loop su (symbol,side) → fetch_recent_reduce_trades
  ccxt bloccante?: Sì × N symbol
  ────────────────────────────────────────
  ├──────────────────────────────────────────────────────┼────────────────────────┼────────────────────────────────────────────────────────────────┼──────────────────────────┤
  │ _run_sync_worker → run_once() = run_reconciliation   │ ogni 8s                │ loop su ordini SENT/ACK → get_order_status                     │ Sì × N ordini            │
  ├──────────────────────────────────────────────────────┼────────────────────────┼────────────────────────────────────────────────────────────────┼──────────────────────────┤
  │ _run_reconciliation_periodically →                   │ 60s (poll_fallback)    │ stesso di sopra                                                │ Sì × N ordini            │
  │ run_reconciliation                                   │                        │                                                                │                          │
  ├──────────────────────────────────────────────────────┼────────────────────────┼────────────────────────────────────────────────────────────────┼──────────────────────────┤
  │ _run_position_reconciliation_periodically (3 metodi  │ ~600s                  │ vedi sotto                                                     │ Sì                       │
  │ in fila)                                             │                        │                                                                │                          │
  ├──────────────────────────────────────────────────────┼────────────────────────┼────────────────────────────────────────────────────────────────┼──────────────────────────┤
  │ → run_position_reconciliation                        │                        │ loop su chain aperte → get_position_qty_with_details (+        │ Sì × N chain             │
  │                                                      │                        │ fetch_recent_reduce_trades)                                    │                          │
  ├──────────────────────────────────────────────────────┼────────────────────────┼────────────────────────────────────────────────────────────────┼──────────────────────────┤
  │ → run_trade_based_reconciliation                     │                        │ loop su (symbol,side) → fetch_recent_reduce_trades             │ Sì × N symbol            │
  ├──────────────────────────────────────────────────────┼────────────────────────┼────────────────────────────────────────────────────────────────┼──────────────────────────┤
  │ → run_protective_orders_reconciliation               │                        │ loop su chain → fetch_position_details                         │ Sì × N chain             │
  ├──────────────────────────────────────────────────────┼────────────────────────┼────────────────────────────────────────────────────────────────┼──────────────────────────┤
  │ WS fill watcher (BybitWsFillWatcher)                 │ continuo               │ websocket fill/order                                           │ (async, separato — da    │
  │                                                      │                        │                                                                │ verificare)              │
  └──────────────────────────────────────────────────────┴────────────────────────┴────────────────────────────────────────────────────────────────┴──────────────────────────┘
