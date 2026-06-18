  Ecco i 5 tipi esistenti con emoji, campi flat, e struttura output:

  ---
  RUNTIME_STARTUP ℹ️  RUNTIME: AVVIATO
  started_at: 2026-06-18 10:00:00 UTC
  ────────────────
  Source: runtime_main
  Payload flat: started_at

  ---
  RUNTIME_SHUTDOWN ℹ️  RUNTIME: SHUTDOWN
  Reason: SIGTERM
  Open chains: 3
  Pending commands: 1
  ────────────────
  Source: runtime_main
  Payload flat: reason, open_chains, pending_commands

  ---
  LISTENER_EDIT_SKIPPED ⚠️  LISTENER: EDIT SKIPPED
  Edit di un segnale con trade chain già creata — non riprocessato.
  Chat: -100123456
  Msg ID: 789
  Edit ts: 2026-06-18T10:00:00
  Action: verifica il messaggio e intervieni manualmente
  ────────────────
  Source: telegram_listener
  Payload flat: description, chat, msg_id, edit_ts, action

  ---
  GATEWAY_ENTRY_ALL_FAILED 🛑 GATEWAY: ENTRY ALL FAILED
  Tutti i comandi PLACE_ENTRY falliti. Catena cancellata.
  Chain: #42
  Symbol: BTC/USDT
  Side: LONG
  Reason: order rejected by exchange
  Action: intervento manuale richiesto
  ────────────────
  Source: execution_gateway
  [link chain]    ← se presente
  Payload flat: description, chain_id, symbol, side, reason, action, link (opzionale)

  ---
  GATEWAY_REVIEW_REQUIRED ⚠️  GATEWAY: REVIEW REQUIRED
  Comando bloccato in REVIEW_REQUIRED.
  Command: PLACE_ENTRY
  Chain: #42
  Reason: ...
  Action: intervento manuale richiesto
  ────────────────
  Source: execution_gateway
  [link chain]    ← se presente
  Payload flat: description, command_type, chain_id, reason, action, link (opzionale)

  ---
  GATEWAY_COMMAND_FAILED 🛑 GATEWAY: COMMAND FAILED (nuovo)
  Comando SL/TP fallito in modo permanente.
  Command: SET_SL
  Chain: #42
  Reason: KeyError: 'order_id'
  ────────────────
  Source: execution_gateway
  Payload flat: command_type, chain_id, command_id, reason

