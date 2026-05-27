 Obiettivo corretto

  Vuoi un sistema che rilevi tutti gli eventi exchange rilevanti per la chain, indipendentemente da come nascono:

  - automatici dal bot
  - automatici exchange-side
  - manuali fatti dall’utente sull’exchange
  - eventi persi durante downtime o disconnessioni

  Questo implica che la fonte di verità non può essere “solo gli ordini emessi dal bot”. Deve essere l’attività reale dell’account sull’exchange.

  Architettura raccomandata

  1. Primary: WebSocket account events
     Deve essere il canale principale perché è il più veloce e il più vicino al fatto.
     Da qui devono arrivare:
      - fills
      - cancel
      - modify
      - position update
      - protective changes, se disponibili
      - chiusure manuali
      - trigger TP/SL attached
  2. Secondary: periodic reconciliation polling
     Deve servire a:
      - recuperare eventi persi dal WS
      - coprire finestre di restart/down
      - correggere drift tra stato locale e stato exchange
      - rilevare azioni manuali che il WS non ha classificato bene
  3. Tertiary: state inference / audit fallback
     Solo quando né WS né polling ti danno un evento classificabile forte.
     Esempi:
      - posizione sparita ma nessun trade recuperabile
      - TP/SL attesi rimossi
      - ordine non più presente

  Principio chiave

  Il sistema non deve più chiedersi:
  “questo evento corrisponde a un comando del bot?”

  Deve prima chiedersi:
  “cosa è successo davvero sull’account exchange?”

  Solo dopo fa il mapping sulla chain locale.

  Questa è la differenza tra:

  - monitoring bot-centric
  - monitoring exchange-centric

  Tu vuoi il secondo.

  Quindi cosa va monitorato davvero

  Per ogni symbol/side/account, il sistema deve saper osservare:

  - trade executions
  - order lifecycle changes
  - position qty changes
  - protective TP/SL presence e mutazioni
  - sparizione/creazione di ordini reduce-only
  - modifiche manuali
  - chiusure manuali
  - fill TP/SL attached
  - cancellazioni manuali di protezioni

  Pipeline ideale

  1. Ingresso WS:
      - watchMyTrades
      - watchOrders
      - eventualmente watchPositions se utile e stabile su Bybit via CCXT Pro
  2. Normalizzazione exchange-native:
     ogni messaggio diventa un evento exchange interno con classificazione forte, ad esempio:
      - ENTRY_FILLED
      - TP_FILLED
      - SL_FILLED
      - MANUAL_CLOSE_PARTIAL
      - MANUAL_CLOSE_FULL
      - PENDING_ENTRY_CANCELLED
      - PROTECTIVE_ORDER_CANCELLED
      - PROTECTIVE_ORDER_MODIFIED
      - POSITION_SIZE_CHANGED_EXTERNALLY
  3. Correlazione con chain locale:
      - match per orderLinkId se esiste
      - altrimenti match per symbol + side + positionIdx + createType + qty/price/time
      - solo come ultimo fallback usare inferenze deboli
  4. Polling periodico:
      - fetchMyTrades
      - fetchOpenOrders
      - fetchPositions
      - opzionale fetchClosedOrders se utile
        Questo polling deve fare reconciliation incrementale, non un semplice “controllo se esiste ancora”.
  5. Audit/fallback:
      - se posizione cambia senza trade associato
      - se protezioni spariscono
      - se c’è divergenza tra open qty locale e exchange qty
        allora creare evento di drift/unknown e portarlo a review o classificazione successiva

  Cosa significa in pratica per la classificazione

  Per distinguere bene gli eventi, l’ordine delle fonti dovrebbe essere:

  1. campi raw exchange espliciti
      - createType
      - stopOrderType
      - execType
      - orderStatus
      - cancelType
      - positionIdx
      - closedSize
      - orderLinkId
  2. correlazione strutturale
      - symbol
      - side
      - qty
      - order id
      - timing
  3. euristiche
      - price match
      - sparizione posizione
      - protezioni mancanti

  Le euristiche non devono più essere il core. Devono essere solo rete di sicurezza.

  Design futuro consigliato

  Separare il problema in 3 moduli chiari:

  - event_ingest_ws
    riceve tutto dal websocket e salva raw normalized events
  - event_classifier
    decide che tipo di evento exchange è, con priorità ai campi raw Bybit
  - event_reconciler
    gira periodicamente e:
      - recupera missing events
      - completa classificazioni deboli
      - rileva drift/manual actions non visti

  Così eviti che ws_fill_watcher.py diventi un mostro con logica mischiata di:

  - parsing
  - matching
  - recovery
  - inferenza

  Manuale vs automatico

  Se vuoi davvero coprire anche il manuale, allora devi introdurre questa distinzione nel modello eventi:

  - source = bot_command
  - source = exchange_auto
  - source = exchange_manual
  - source = reconciliation_inferred

  Perché un CLOSE_FULL_FILLED manuale non è semanticamente uguale a un TP_FILLED o a un SL_FILLED, anche se tutti portano qty -> 0.

  Roadmap concreta

  Io la farei così:

  1. Rafforzare classificazione fills da watchMyTrades con campi raw Bybit
  2. Allineare fetchMyTrades polling alla stessa logica
  3. Aggiungere reconciliation di posizioni/protezioni come fallback, non come sorgente primaria
  4. Introdurre eventi “manual/external” distinti
  5. Solo dopo ripulire il legacy SET_POSITION_TPSL_PARTIAL

  Sintesi

  Il modello che vuoi è corretto:

  - WS primario
  - polling secondario di recovery/controllo
  - fallback inference terziario

  La cosa importante è far diventare il sistema exchange-centric:
  non monitorare solo gli ordini del bot, ma tutto ciò che succede davvero sull’account.