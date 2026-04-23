# Parser Notes

## Contesto

Questo file raccoglie solo promemoria e questioni aperte relative al parser,
alla semantica degli intent e al contratto parser -> runtime.

## Promemoria aperti

### 1. Centralizzare piu logica nel parser/core

Oggi alcune semantiche critiche sono ancora duplicate nei profili trader
specifici invece che normalizzate nel core centrale.

Caso concreto gia emerso:
- `U_MOVE_STOP_TO_BE` in `trader_c` veniva riconosciuto come intent ma non
  materializzava sempre `entities.new_stop_level = "ENTRY"`.

Direzione desiderata:
- spostare piu normalizzazione semantica nel layer parser centrale/canonico
- ridurre la duplicazione tra `trader_a`, `trader_b`, `trader_c`, `trader_d`
- lasciare ai profili trader soprattutto estrazione locale e pattern matching

### 2. Validazione forte prima del livello successivo

Serve un hardening del layer canonico/validation prima del passaggio ai moduli
successivi.

Controlli desiderati:
- `intent -> campi obbligatori`
- `U_MOVE_STOP_TO_BE` richiede `new_stop_level`
- `U_MOVE_STOP` richiede un livello coerente
- gli update incompleti non devono arrivare silenziosamente a resolver/runtime

Nota architetturale:
- questa validazione idealmente dovrebbe vivere in modelli tipizzati /
  validatori centrali, non essere lasciata solo ai parser trader-specifici

### 3. Rivedere la logica di gestione intent ambigui

Va rivista la logica nei casi in cui il testo contiene segnali sia da
`U_MOVE_STOP_TO_BE` sia da `U_MOVE_STOP`.

Caso reale osservato:
- messaggio in stile "BE" con prezzo esplicito
- il parser oggi puo produrre entrambi:
  - `U_MOVE_STOP_TO_BE`
  - `U_MOVE_STOP`

Questione aperta da decidere esplicitamente:
- se il testo contiene un prezzo esplicito, la priorita semantica deve andare a
  `U_MOVE_STOP`?
- oppure il linguaggio "BE" deve continuare a dominare, trattando il prezzo
  come hint/accessorio?

Questa scelta va formalizzata nel contratto parser, non lasciata implicita.

### 4. Allineare parser intent e runtime bot

Va rivista insieme la logica di gestione intent e il lato bot esecutivo.

Caso reale BTC osservato:
- il segnale aveva range `66200-66100`
- il messaggio update parlava di BE e conteneva anche `66200`
- il trade reale e stato riempito a `66222.6`

Problema emerso:
- se il runtime usa il piano segnale o il prezzo testuale come source of truth,
  il BE non e davvero breakeven

Regola desiderata lato bot:
- quando il significato operativo e "move stop to BE", il prezzo esecutivo deve
  venire dal fill reale del trade
- prezzo del messaggio e prezzi del segnale possono restare utili come audit o
  hint, ma non devono battere il fill reale per l'esecuzione

### 5. Definire il contratto canonico per STOP moves

Serve una decisione chiara su come rappresentare i move-stop nel payload
normalizzato.

Possibile direzione:
- `U_MOVE_STOP_TO_BE`
  - `new_stop_level = "ENTRY"`
  - opzionale `new_stop_price_hint`
- `U_MOVE_STOP`
  - `new_stop_level = <prezzo o livello canonico>`
- se il testo e ambiguo:
  - definire una precedence esplicita
  - oppure emettere un solo intent canonico con metadata di motivazione

## Obiettivo

Ridurre i casi in cui:
- il parser capisce il messaggio ma il runtime lo interpreta in modo diverso
- intent simili (`U_MOVE_STOP` vs `U_MOVE_STOP_TO_BE`) creano ambiguita
- il bot applica prezzi non coerenti con il fill reale
