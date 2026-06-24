Configurazione telegram_control.yaml
Prerequisiti
Un bot Telegram (token da @BotFather)
Uno o più supergroup Telegram con il bot aggiunto come admin
I topic (thread) creati dentro ogni supergroup
Come trovare i valori
chat_id del supergroup
Aggiungi @userinfobot al supergroup, scrivi qualcosa — risponde con il chat_id (numero negativo tipo -1001234567890).

thread_id di un topic
Apri il topic → click su un messaggio → "Copy Link" → l'URL è tipo https://t.me/c/1234567890/42 → il numero finale (42) è il thread_id.

user_id dell'utente autorizzato
Scrivi /start a @userinfobot in chat privata — risponde con il tuo id.

Struttura del config
Il file ha quattro sezioni:

CONNESSIONE   → token del bot, chi può dare comandi
AVVIO         → comportamento allo startup
ROUTING       → dove scrivere (chat_id, thread_id per account e trader)
NOTIFICHE     → quali eventi generano un messaggio
La chiave di routing: account.id
Ogni trader ha un file config/traders/<nome>.yaml con una sezione:

account:
  id: "main"   # ← questa stringa è il collegamento
Questa stringa deve corrispondere esattamente a una chiave in per_account. Se non corrisponde, il sistema usa default_account come fallback.

I tre casi di configurazione
Caso 1 — Un exchange account, più trader

Tutti i trader girano sullo stesso conto exchange. Vuoi un topic clean_log separato per ciascuno, ma tech_log e commands sono condivisi.

default_account: main

per_account:
  main:
    chat_id: -1001234567890
    topics:
      clean_log:
        thread_id: 11          # fallback per trader non mappati
        per_trader:
          sma_intraday:  101
          rsi_swing:     102
          rsi_intraday:  103
      tech_log:   {thread_id: 3}
      commands:   {thread_id: 13}
Tutti i trader yaml hanno account.id: "main".

Caso 2 — Più exchange account, più trader per account

Hai due o più conti exchange distinti (API key separate). Ogni account ha il suo tech_log e i suoi comandi.

default_account: main

per_account:
  main:
    chat_id: -1001234567890
    topics:
      clean_log:
        thread_id: 11
        per_trader:
          trader_a:  101
          trader_b:  102
      tech_log:   {thread_id: 3,   min_level: INFO, operational_events: true}
      commands:   {thread_id: 13}

  sub_a:
    chat_id: -1001234567890    # stesso supergroup o uno diverso
    topics:
      clean_log:
        thread_id: 201
        per_trader:
          trader_x:  211
          trader_y:  212
      tech_log:   {thread_id: 203, min_level: INFO, operational_events: true}
      commands:   {thread_id: 213}
I trader di main hanno account.id: "main", quelli di sub_a hanno account.id: "sub_a".

Caso 3 — Exchange account dedicato per singolo trader

Un conto exchange con un solo trader. Non serve per_trader.

per_account:
  sub_b:
    chat_id: -1001234567890
    topics:
      clean_log:  {thread_id: 301}
      tech_log:   {thread_id: 303, min_level: INFO, operational_events: true}
      commands:   {thread_id: 313}
Il trader yaml ha account.id: "sub_b".

I casi si combinano: puoi avere main (caso 1) + sub_a (caso 2) + sub_b (caso 3) nello stesso file.

Sezione notifiche
Controlla quali eventi generano un messaggio Telegram. I valori possibili sono "on", "off", "silent" (inviato senza notifica sonora).

notifications:
  startup:                "on"
  shutdown:               "on"
  control_change:         "on"
  review_required:        "on"
  entry_order_placed:     "silent"
  entry_filled:           "on"
  tp_filled:              "on"
  sl_filled:              "on"
  close_full_filled:      "on"
  close_partial_filled:   "on"
  order_rejected:         "on"
  reconciliation_warning: "on"
  technical_error:        "on"
Parametri di tuning (opzionali)
Ometti questa sezione per usare i default. Decommentala solo se vuoi cambiare un valore specifico.

Parametro	Default	Descrizione
clean_log.debounce_seconds	20	Pausa minima tra messaggi della stessa chain
clean_log.aggregate_fills_seconds	30	Finestra aggregazione fill multipli
clean_log.max_messages_per_chain_per_minute	4	Rate limit per chain
clean_log.min_partial_fill_notify_pct	10	Soglia minima fill parziale da notificare
tech_log.min_level	WARNING	Livello minimo log (DEBUG, INFO, WARNING, ERROR)
tech_log.max_messages_per_minute	20	Rate limit tech log per account
tech_log.dedupe_window_seconds	60	Finestra deduplicazione messaggi identici
tech_log.debug_max_duration_minutes	60	Durata massima modalità debug
