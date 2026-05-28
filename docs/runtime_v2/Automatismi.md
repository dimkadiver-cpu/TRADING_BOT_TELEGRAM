  Automatismi del sistema — mappa completa

  1. All'arrivo di un SIGNAL (nuovo segnale Telegram)

  Trigger: enriched_canonical_messages con primary_class=SIGNAL e enrichment_decision=PASS

  ┌────────────────────────────────┬───────────────────────────────────────────────────────────────────────────────────────────┬─────────────────────┐
  │          Automatismo           │                                       Cosa succede                                        │        Dove         │
  ├────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────┼─────────────────────┤
  │ Risk check                     │ Valida capitale, leverage, leva residua; se fallisce → REVIEW_REQUIRED                    │ RiskCapacityEngine  │
  ├────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────┼─────────────────────┤
  │ PLACE_ENTRY_WITH_ATTACHED_TPSL │ Piazza ordine di entrata (MARKET/LIMIT) con TP e SL attached in un'unica chiamata         │ EntryCommandFactory │
  ├────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────┼─────────────────────┤
  │ Timeout scheduling             │ Se cancel_pending_on_timeout=True, imposta entry_timeout_at = now + pending_timeout_hours │ LifecycleEntryGate  │
  ├────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────┼─────────────────────┤
  │ Control mode check             │ Se BLOCK_NEW_ENTRIES o FULL_STOP → REVIEW_REQUIRED, nessun ordine                         │ LifecycleEntryGate  │
  └────────────────────────────────┴───────────────────────────────────────────────────────────────────────────────────────────┴─────────────────────┘

  ---
  2. All'ENTRY_FILL (conferma fill sull'exchange)

  Trigger: evento ENTRY_FILLED da WS fill watcher

  ┌────────────────────────────────────────────────┬─────────────────────────────────────────────────────────────────────────────────────────────────────┬────────────────────────────────────────────────┐
  │                  Automatismo                   │                                            Cosa succede                                             │                      Dove                  │
  ├────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────┤
  │ Aggiorna entry_avg_price                       │ Ricalcola la media ponderata delle fill                                                             │ event_processor._process_entry_filled          │
  ├────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────┤
  │ Aggiorna qty (filled_entry_qty,                │ Traccia le quantità eseguite                                                                        │ idem                  │
  │ open_position_qty)                             │                                                                                                     │                  │
  ├────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────┤
  │ Ricalcola risk (risk_already_realized,         │ Ogni fill consuma rischio proporzionalmente                                                         │ idem                  │
  │ risk_remaining)                                │                                                                                                     │                  │
  ├────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────┤
  │ WAITING_POSITION → PENDING                     │ Sblocca i comandi TP/SL in attesa di posizione aperta                                               │ workers._persist_result →                  │
  │                                                │                                                                                                     │ release_waiting_position                  │
  ├────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────┤
  │ REBUILD_PARTIAL_TPS                            │ Se rebuild_policy=ON_EACH_ENTRY_FILL e ci sono intermediate_tps, piazza i TP parziali               │ PostFillProtectionRebuilder                  │
  ├────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────┤
  │ Deferred BE trigger                            │ Se il flag _be_deferred_by_auto_cancel è presente e non ci sono più averaging legs pendenti, emette │ event_processor._process_entry_filled          │
  │                                                │  MOVE_STOP_TO_BREAKEVEN                                                                             │                  │
  └────────────────────────────────────────────────┴─────────────────────────────────────────────────────────────────────────────────────────────────────┴────────────────────────────────────────────────┘

  ---
  3. Al TP parziale (non-final TP)

  Trigger: evento TP_FILLED con is_final=False

  ┌────────────────────────┬───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┬────────────────────────────────────┐
  │      Automatismo       │                                                               Cosa succede                                                                │Dove                │
  ├────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────┤
  │ SYNC_PROTECTIVE_ORDERS │ Riallinea SL/TP sull'exchange alla nuova quantità residua                                                                                 │ event_processor._process_tp_filled │
  ├────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────┤
  │ MOVE_STOP_TO_BREAKEVEN │ Se be_trigger = "tp{N}" e questo è il TP N → sposta lo stop a breakeven                                                                   │ idem                    │
  ├────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────┤
  │ Auto-cancel averaging  │ Se cancel_averaging_pending_after = "tp{N}" e ci sono averaging legs pendenti → emette CANCEL_PENDING_ENTRY con                           │ idem                    │
  │                        │ cancel_reason=auto_cancel_averaging                                                                                                       │                    │
  ├────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────┤
  │ Deferred BE            │ Se il BE doveva scattare su questo TP ma c'erano averaging legs attive → setta flag _be_deferred_by_auto_cancel, BE rimandato a quando le │ idem                    │
  │                        │  cancel sono confermate                                                                                                                   │                    │
  └────────────────────────┴───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┴────────────────────────────────────┘

  ---
  4. Al TP finale / SL hit

  Trigger: TP_FILLED con is_final=True oppure SL_FILLED

  ┌─────────────────┬──────────────────────────────────────────────────────────────────────────────────────────────┬─────────────────┐
  │   Automatismo   │                                         Cosa succede                                         │      Dove       │
  ├─────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────┼─────────────────┤
  │ Chiude la chain │ lifecycle_state → CLOSED, open_position_qty → 0.0                                            │ event_processor │
  ├─────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────┼─────────────────┤
  │ Nessun comando  │ Il fill finale conclude tutto; SL e TP attached vengono cancellati dall'exchange nativamente │ idem            │
  └─────────────────┴──────────────────────────────────────────────────────────────────────────────────────────────┴─────────────────┘

  ---
  5. Al CLOSE_FULL manuale (da Telegram update)

  Trigger: update Telegram con action CLOSE_FULL

  ┌──────────────────────────────────────┬─────────────────────────────────────────────────────────────────────────────────────────────┬──────────────────────────────────────────────────────────────────┐
  │             Automatismo              │                                        Cosa succede                                         │                               Dove                  │
  ├──────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────┤
  │ CLOSE_FULL                           │ Emette comando di chiusura dell'intera posizione a mercato                                  │ entry_gate._apply_close_full                  │
  ├──────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────┤
  │ CANCEL_PENDING_ENTRY (1° —           │ Cancella eventuali ordini limit di entry ancora aperti, emesso contestualmente al           │ idem, cancel_pending_for_close:{chain_id}:{cmid}                 │
  │ preventivo)                          │ CLOSE_FULL                                                                                  │                  │
  ├──────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────┤
  │ CANCEL_PENDING_ENTRY (2° —           │ Dopo che CLOSE_FULL_FILLED è confermato, emette un secondo cancel per pulire eventuali      │ event_processor._process_close_full_filled,                  │
  │ post-conferma)                       │ ordini sfuggiti al primo                                                                    │ cancel_on_close:{chain_id}                  │
  └──────────────────────────────────────┴─────────────────────────────────────────────────────────────────────────────────────────────┴──────────────────────────────────────────────────────────────────┘

  ▎ Questo è il meccanismo che hai visto su chain 4 (cmd_id=12 + cmd_id=13).

  ---
  6. Al CLOSE_PARTIAL manuale

  Trigger: update con action CLOSE_PARTIAL

  ┌────────────────────────┬───────────────────────────────────────────────────────────┬───────────────────────────────────────────────┐
  │      Automatismo       │                       Cosa succede                        │                     Dove                      │
  ├────────────────────────┼───────────────────────────────────────────────────────────┼───────────────────────────────────────────────┤
  │ CLOSE_PARTIAL          │ Chiude una frazione della posizione                       │ entry_gate._apply_close_partial               │
  ├────────────────────────┼───────────────────────────────────────────────────────────┼───────────────────────────────────────────────┤
  │ SYNC_PROTECTIVE_ORDERS │ Riallinea TP/SL alla nuova quantità dopo il fill parziale │ event_processor._process_close_partial_filled │
  └────────────────────────┴───────────────────────────────────────────────────────────┴───────────────────────────────────────────────┘

  ---
  7. Al CANCEL_PENDING (da Telegram update)

  Trigger: update con action CANCEL_PENDING

  ┌────────────────────────┬────────────────────────────────────────────────────────────────────────────────────────────────────────────┬────────────────────────────────────────────────────────────┐
  │      Automatismo       │                                                Cosa succede                                                │                            Dove             │
  ├────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────┤
  │ CANCEL_PENDING_ENTRY   │ Cancella sull'exchange tutti gli ordini limit di entry pendenti                                            │ entry_gate._apply_cancel_pending             │
  ├────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────┤
  │ SYNC_PROTECTIVE_ORDERS │ Solo se la posizione è già aperta e la modalità non è UNIFIED_PLAN → riallinea la protezione               │ idem             │
  ├────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────┤
  │ Conferma BE differito  │ Se era in attesa di deferred BE, lo emette dopo che le cancel sono confermate                              │ event_processor._process_pending_entry_cancelled_confirmed │
  ├────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────┤
  │ → CANCELLED            │ Se la posizione non era mai stata aperta e non ci sono entry in volo → chiude la chain con stato CANCELLED │ idem             │
  └────────────────────────┴────────────────────────────────────────────────────────────────────────────────────────────────────────────┴────────────────────────────────────────────────────────────┘

  ---
  8. Al MOVE_STOP (breakeven da Telegram update)

  Trigger: update con action SET_STOP target ENTRY

  ┌──────────────────────────┬──────────────────────────────────────────────────────────────────┬──────────────────────────────┐
  │       Automatismo        │                           Cosa succede                           │             Dove             │
  ├──────────────────────────┼──────────────────────────────────────────────────────────────────┼──────────────────────────────┤
  │ MOVE_STOP_TO_BREAKEVEN   │ Calcola il prezzo BE (avg_price + fee correction) e sposta lo SL │ entry_gate._apply_move_to_be │
  ├──────────────────────────┼──────────────────────────────────────────────────────────────────┼──────────────────────────────┤
  │ Guard: già protetto      │ Se be_protection_status=PROTECTED → NOOP, nessun comando         │ idem                         │
  ├──────────────────────────┼──────────────────────────────────────────────────────────────────┼──────────────────────────────┤
  │ Guard: comando duplicato │ Se c'è già un MOVE_STOP_TO_BREAKEVEN in volo → NOOP              │ idem                         │
  └──────────────────────────┴──────────────────────────────────────────────────────────────────┴──────────────────────────────┘

  ---
  9. Timeout automatico (pending non fillato)

  Trigger: TimeoutWorker — polling su chain con entry_timeout_at < now in stato WAITING_ENTRY

  ┌──────────────────────┬──────────────────────────────────────────────────────────────────┬───────────────────────┐
  │     Automatismo      │                           Cosa succede                           │         Dove          │
  ├──────────────────────┼──────────────────────────────────────────────────────────────────┼───────────────────────┤
  │ → EXPIRED            │ La chain passa in stato EXPIRED                                  │ workers.TimeoutWorker │
  ├──────────────────────┼──────────────────────────────────────────────────────────────────┼───────────────────────┤
  │ CANCEL_PENDING_ENTRY │ Cancella tutti gli ordini di entry pendenti associati alla chain │ idem                  │
  └──────────────────────┴──────────────────────────────────────────────────────────────────┴───────────────────────┘

  ---
  10. Modifica entries (da Telegram update)

  Trigger: update con action MODIFY_ENTRIES
  │ CANCEL_PENDING_ENTRY │ Cancella tutti gli ordini di entry pendenti associati alla chain │ idem                  │
  └──────────────────────┴──────────────────────────────────────────────────────────────────┴───────────────────────┘

  ---
  10. Modifica entries (da Telegram update)

  Trigger: update con action MODIFY_ENTRIES

  ┌────────────────────────────────┬──────────────────────────────────────────────────────────────────────────────┬──────────────────────────────────┐
  │          Automatismo           │                                 Cosa succede                                 │               Dove               │
  ├────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────┤
  │ Diff engine                    │ Confronta il piano corrente con il piano target richiesto                    │ ExecutionPlanDiffEngine          │
  ├────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────┤
  │ CANCEL_PENDING_ENTRY           │ Per ogni leg da cancellare → cancella l'ordine specifico per client_order_id │ entry_gate._apply_modify_entries │
  ├────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────┤
  │ PLACE_ENTRY_WITH_ATTACHED_TPSL │ Per ogni leg da sostituire → piazza il nuovo ordine con nuovi parametri      │ idem                             │
  └────────────────────────────────┴──────────────────────────────────────────────────────────────────────────────┴──────────────────────────────────┘

  ---
  Riepilogo comandi che il sistema può emettere automaticamente

  PLACE_ENTRY_WITH_ATTACHED_TPSL   — apertura posizione
  PLACE_ENTRY                       — leg aggiuntiva (D_POSITION_TPSL mode)
  CANCEL_PENDING_ENTRY              — cancel ordini pending (5 trigger diversi)
  CLOSE_FULL                        — chiusura completa manuale
  CLOSE_PARTIAL                     — chiusura parziale manuale
  MOVE_STOP_TO_BREAKEVEN            — sposta SL a breakeven
  SYNC_PROTECTIVE_ORDERS            — riallinea TP/SL alla qty residua
  REBUILD_PARTIAL_TPS               — ricostruisce TP parziali dopo fill
  SET_POSITION_TPSL_FULL            — (D_POSITION mode) imposta TP+SL full
  SET_POSITION_TPSL_PARTIAL         — (D_POSITION mode) imposta TP+SL parziale