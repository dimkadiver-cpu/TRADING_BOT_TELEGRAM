# Fase 4 - Regole rischio e dimensione posizione

## Scopo

Questo file riassume in modo semplice come dovrebbe funzionare la parte di rischio nella Fase 4.

L'idea base e':

- non si decide prima la size
- si decide prima quanto si e' disposti a perdere
- la size della posizione viene calcolata dal sistema in base a:
  - capitale
  - entry
  - stop loss
  - leva

---

## Principio operativo

Il trader o la configurazione non devono dire:

- "apri sempre il 2% del capitale"

Devono invece dire una di queste due cose:

- "per ogni trade posso perdere al massimo lo 0.5% del capitale"
- "per ogni trade posso perdere al massimo 10 USDT"

Da li' il sistema calcola automaticamente la dimensione corretta della posizione.

---

## Cosa si imposta

### 1. Impostazioni globali

Queste valgono per tutti i trader, salvo override specifico.

- `enabled`
  - attiva o disattiva il trader di default
- `gate_mode`
  - `block`: se una regola fallisce, il segnale viene bloccato
  - `warn`: il segnale passa ma con warning
- `risk_mode`
  - `risk_pct_of_capital`
  - `risk_usdt_fixed`
- `risk_pct_of_capital`
  - percentuale massima di capitale che si puo' perdere su un singolo trade
- `risk_usdt_fixed`
  - importo fisso massimo che si puo' perdere su un singolo trade
- `capital_base_mode`
  - `static_config`: usa un capitale scritto in config
  - `live_equity`: in futuro usera' il capitale reale live
- `capital_base_usdt`
  - capitale di riferimento se si usa `static_config`
- `leverage`
  - leva da applicare al trade
- `max_capital_at_risk_pct`
  - rischio massimo totale consentito su tutti i trade aperti
- `hard_max_per_signal_risk_pct`
  - limite assoluto di sicurezza sul rischio di un singolo segnale
  - da confermare: tenerlo come hard cap separato oppure eliminarlo se basta `risk_pct_of_capital`
- `max_capital_at_risk_per_trader_pct`
  - rischio massimo totale consentito per un singolo trader
- `max_concurrent_same_symbol`
  - numero massimo di trade aperti sullo stesso simbolo per trader
- `entry_split`
  - regola di distribuzione fra piu' entry
- `tp_handling_mode`
  - decide se seguire tutti i TP del segnale o fermarsi a un numero massimo
- `max_tp_levels`
  - numero massimo di TP da seguire se si vuole limitare
- `tp_close_distribution`
  - regola di distribuzione delle chiusure parziali sui TP
  - da confermare: distribuzione automatica oppure regole separate per 2 TP, 3 TP, 5 TP
- `price_sanity`
  - controlli base sui prezzi fuori range
- `position_management`
  - snapshot delle regole di gestione posizione da passare al Sistema 1

### 2. Impostazioni per singolo trader

Ogni trader puo' avere un override delle impostazioni globali.

Per esempio un trader puo':

- usare rischio in percentuale
- un altro usare rischio fisso in USDT
- un altro ancora usare una leva diversa
- avere limiti piu' stretti su simboli e rischio totale

---

## Cosa NON si imposta a mano

Questi valori devono essere calcolati dal sistema:

- `position_size_usdt`
- `position_size_pct`
- `risk_budget_usdt`
- `sl_distance_pct`

In altre parole:

- il trader dice quanto vuole rischiare
- il sistema calcola quanto grande deve essere la posizione

---

## Modalita' rischio

### Modalita' 1 - rischio come percentuale del capitale

Significato:

- "sono disposto a perdere al massimo X% del capitale"

Esempio:

- capitale = 100 USDT
- rischio = 1%
- perdita massima consentita = 1 USDT

### Modalita' 2 - rischio come valore fisso

Significato:

- "sono disposto a perdere al massimo X USDT"

Esempio:

- rischio fisso = 10 USDT
- non importa se il capitale e' 100 o 1000
- la perdita massima su quel trade resta 10 USDT

---

## Come si calcola la posizione

### Passo 1 - si calcola il budget di rischio

Se modalita' = percentuale capitale:

- budget rischio = capitale x percentuale rischio

Se modalita' = valore fisso:

- budget rischio = valore fisso impostato

### Passo 2 - si calcola la distanza tra entry e stop

La distanza stop e' la distanza percentuale tra:

- prezzo di entrata
- stop loss

### Passo 3 - si calcola la size della posizione

La size dipende da:

- budget rischio
- distanza stop
- leva

Regola pratica:

- stop stretto -> posizione piu' grande
- stop largo -> posizione piu' piccola
- leva piu' alta -> posizione base piu' piccola per mantenere lo stesso rischio

---

## Esempio semplice

Capitale:

- 100 USDT

Rischio:

- 1% del capitale

Quindi perdita massima:

- 1 USDT

Leva:

- x5

Caso A:

- distanza stop = 1%

Risultato:

- il sistema puo' permettersi una posizione piu' grande

Caso B:

- distanza stop = 5%

Risultato:

- il sistema deve aprire una posizione piu' piccola

Quindi:

- a parita' di rischio massimo
- la posizione cambia sempre in base allo stop loss

---

## Regole di blocco o warning

Il sistema deve poter bloccare o segnalare warning nei seguenti casi.

### Regole minime

- trader disabilitato
- manca stop loss
- manca entry valida
- leva non valida
- distanza stop uguale a zero o impossibile da calcolare
- posizione calcolata troppo grande
- rischio singolo trade sopra il limite massimo
- rischio totale trader sopra il limite massimo
- rischio totale globale sopra il limite massimo
- troppi trade aperti sullo stesso simbolo
- prezzo fuori range statico se il controllo e' attivo

### Comportamento

- `block`: il segnale non diventa operativo
- `warn`: il segnale resta operativo ma viene marcato con warning

---

## Gestione di piu' entry

Se il segnale ha piu' entry, bisogna definire come ricavare il prezzo di riferimento per il rischio.

Le opzioni possibili sono:

- media semplice delle entry
- media pesata secondo `entry_split`
- prima entry soltanto

La scelta consigliata e':

- media pesata, se esiste una distribuzione chiara delle entry

Se non c'e' una regola esplicita:

- usare una media semplice come fallback

---

## Gestione dei Take Profit

Anche per i TP bisogna separare due cose:

- i TP presenti nel segnale del trader
- le regole con cui il sistema gestisce la chiusura della posizione

### 1. TP presenti nel segnale

Il trader puo' mandare:

- 2 TP
- 3 TP
- 5 TP
- 6 TP

La Fase 4 non decide i livelli prezzo dei TP.

La Fase 4:

- legge i TP estratti dal parser
- decide quanti TP seguire davvero
- salva le regole di gestione da passare al Sistema 1

### 2. Regole globali da poter impostare

Le impostazioni utili sono queste:

- `tp_handling_mode`
  - `follow_all_signal_tps`
    - segue tutti i TP presenti nel segnale del trader
  - `limit_to_max_levels`
    - segue solo i primi TP fino a un massimo definito
- `max_tp_levels`
  - esempio: 2, 3, 5
- `tp_close_distribution`
  - distribuzione delle chiusure parziali sui TP seguiti

Queste regole devono esistere a livello:

- globale
- singolo trader, come override

### 3. Esempi pratici

#### Caso A - il segnale ha 2 TP

TP del trader:

- TP1
- TP2

Possibili regole:

- TP1 -> chiudi 50%
- TP2 -> chiudi 50%

Oppure:

- TP1 -> chiudi 70%
- TP2 -> chiudi 30%

#### Caso B - il segnale ha 3 TP

TP del trader:

- TP1
- TP2
- TP3

Possibili regole:

- TP1 -> chiudi 30%
- TP2 -> chiudi 30%
- TP3 -> chiudi 40%

Oppure:

- TP1 -> chiudi 50%
- TP2 -> sposta stop a BE
- TP3 -> chiudi tutto il resto

#### Caso C - il segnale ha 5 TP

TP del trader:

- TP1
- TP2
- TP3
- TP4
- TP5

Possibili regole se si seguono tutti:

- TP1 -> chiudi 20%
- TP2 -> chiudi 20%
- TP3 -> chiudi 20%
- TP4 -> chiudi 20%
- TP5 -> chiudi 20%

Oppure:

- TP1 -> chiudi 30%
- TP2 -> chiudi 20%
- TP3 -> chiudi 20%
- TP4 -> chiudi 10%
- TP5 -> chiudi 20%

### 4. Esempi di limitazione del numero di TP

#### Esempio 1 - seguire tutti i TP del segnale

Configurazione:

- `tp_handling_mode = follow_all_signal_tps`

Effetto:

- se il trader manda 2 TP, il sistema segue 2 TP
- se il trader manda 3 TP, il sistema segue 3 TP
- se il trader manda 5 TP, il sistema segue 5 TP

#### Esempio 2 - limitare tutto a 3 TP

Configurazione:

- `tp_handling_mode = limit_to_max_levels`
- `max_tp_levels = 3`

Effetto:

- se il trader manda 2 TP, il sistema segue 2 TP
- se il trader manda 3 TP, il sistema segue 3 TP
- se il trader manda 5 TP, il sistema segue solo i primi 3

#### Esempio 3 - limitare tutto a 2 TP

Configurazione:

- `tp_handling_mode = limit_to_max_levels`
- `max_tp_levels = 2`

Effetto:

- se il trader manda 5 TP, il sistema usa solo TP1 e TP2

### 5. Regola importante sulla distribuzione percentuale

Se si usano chiusure parziali ai TP, bisogna decidere come distribuire il totale.

La somma delle percentuali dovrebbe essere:

- 100% se si vuole chiudere tutta la posizione attraverso i TP

Esempi validi:

- 50 + 50
- 30 + 30 + 40
- 20 + 20 + 20 + 20 + 20

Esempi da evitare:

- 30 + 30
  - lascia il 40% senza regola
- 60 + 60
  - supera il 100%

### 6. Rapporto tra TP del segnale e regole di gestione

I TP del segnale dicono:

- dove sono i target

Le regole di gestione dicono:

- cosa fare quando ogni target viene raggiunto

Quindi:

- i livelli TP arrivano dal trader
- le percentuali di chiusura arrivano dalla tua configurazione

### 7. Comportamento consigliato

Per tenere il sistema semplice:

- impostare la gestione TP a livello globale
- permettere override per trader particolari
- decidere se seguire tutti i TP o solo i primi N
- definire sempre una distribuzione di chiusura coerente con il numero di TP seguiti

### 8. Punto da confermare

Prima di implementare, va deciso:

- se la distribuzione deve adattarsi automaticamente al numero di TP reali del segnale
- oppure se deve esistere una distribuzione fissa per 2 TP, una per 3 TP, una per 5 TP

La soluzione piu' chiara e controllabile e':

- definire regole separate per 2 TP
- definire regole separate per 3 TP
- definire regole separate per 5 TP

---

## Tabella rapida TP

| Caso | TP nel segnale | TP seguiti dal sistema | Esempio distribuzione chiusura | Nota |
|---|---|---|---|---|
| Caso 1 | 2 TP | 2 | 50 / 50 | semplice e lineare |
| Caso 2 | 3 TP | 3 | 30 / 30 / 40 | adatto a gestione graduale |
| Caso 3 | 5 TP | 5 | 20 / 20 / 20 / 20 / 20 | segue tutto il segnale |
| Caso 4 | 5 TP | 3 | 40 / 30 / 30 | usa solo i primi 3 TP |
| Caso 5 | 6 TP | 2 | 50 / 50 | modalita' molto semplificata |

### Scelte principali da fare

| Scelta | Opzioni semplici | Domanda pratica |
|---|---|---|
| Quanti TP seguire | tutti / primi 2 / primi 3 / primi 5 | voglio copiare tutto il trader o semplificare? |
| Come chiudere ai TP | percentuali fisse | voglio distribuire la chiusura in modo regolare o aggressivo? |
| Regola globale o per trader | globale / override trader | tutti i trader uguali o alcuni con logica diversa? |
| Adattamento automatico | si / no | da confermare: la distribuzione cambia da sola in base al numero di TP reali? |

### Configurazioni consigliate da valutare

| Profilo | Regola consigliata |
|---|---|
| Semplice | seguire solo i primi 2 TP |
| Intermedio | seguire i primi 3 TP |
| Completo | seguire tutti i TP del segnale |
| Trader con molti TP come trader_3 | seguire tutti o almeno i primi 5 |

---

## Tabella rapida Entry e Averaging

| Caso | Entry nel segnale | Regola allocazione | Esempio | Nota |
|---|---|---|---|---|
| Caso 1 | 1 entry | 100% su entry unica | E1 = 100% | caso piu' semplice |
| Caso 2 | 2 entry | principale + averaging | E1 = 70%, E2 = 30% | molto utile per setup con entry principale e recupero |
| Caso 3 | 3 entry | distribuzione decrescente | E1 = 50%, E2 = 30%, E3 = 20% | priorita' alla prima entry |
| Caso 4 | 3 entry | distribuzione uguale | E1 = 33%, E2 = 33%, E3 = 34% | piu' neutrale |
| Caso 5 | zona con 2 estremi | split zona | LOW = 50%, HIGH = 50% | oppure midpoint o three_way |

### Scelte principali sulle entry

| Scelta | Opzioni semplici | Domanda pratica |
|---|---|---|
| Quante entry seguire | tutte / prime 2 / prime 3 | voglio copiare tutto il piano del trader? |
| Come allocare il rischio | uguale / decrescente / pesi fissi | voglio caricare di piu' la prima entry o distribuire? |
| Prezzo di riferimento per il rischio | media semplice / media pesata / prima entry | da confermare: su quale base voglio calcolare il rischio reale? |
| Regola globale o per trader | globale / override trader | tutti i trader uguali o alcuni con logica diversa? |

### Configurazioni consigliate da valutare

| Profilo | Regola consigliata |
|---|---|
| Semplice | 1 entry = 100% |
| Standard con averaging | 2 entry = 70 / 30 |
| Trader piu' aggressivo | 3 entry = 50 / 30 / 20 |
| Trader con zone | endpoints 50 / 50 oppure three_way |

---

## Tabella rapida Modalita' rischio

| Modalita' | Cosa imposti tu | Cosa calcola il sistema | Quando usarla |
|---|---|---|---|
| `risk_pct_of_capital` | percentuale massima che puoi perdere | budget rischio in USDT e size posizione | se vuoi rischio proporzionale al conto |
| `risk_usdt_fixed` | perdita massima fissa in USDT | size posizione | se vuoi perdere sempre lo stesso importo |
| `size_pct` | percentuale di capitale allocata alla posizione | rischio risultante | modello attuale, meno adatto al tuo obiettivo |

### Esempi rapidi

| Capitale | Modalita' | Valore impostato | Perdita massima |
|---|---:|---:|---:|
| 100 USDT | `risk_pct_of_capital` | 1% | 1 USDT |
| 100 USDT | `risk_pct_of_capital` | 0.5% | 0.5 USDT |
| 100 USDT | `risk_usdt_fixed` | 10 USDT | 10 USDT |
| 1000 USDT | `risk_usdt_fixed` | 10 USDT | 10 USDT |

### Scelta consigliata

| Obiettivo | Modalita' consigliata |
|---|---|
| voglio rischiare sempre una parte del conto | `risk_pct_of_capital` |
| voglio rischiare sempre lo stesso importo | `risk_usdt_fixed` |
| voglio solo allocare size e non controllare davvero la perdita | `size_pct` solo per compatibilita' con il modello attuale |

---

## Tabella rapida Gate e Blocchi

| Regola | Quando scatta | Azione consigliata |
|---|---|---|
| trader disabilitato | trader non attivo | `block` |
| stop loss mancante | non si puo' calcolare il rischio | `block` |
| entry mancante | non si puo' calcolare la size | `block` |
| leva non valida | leva zero o incoerente | `block` |
| distanza stop zero | entry uguale a stop | `block` |
| rischio singolo troppo alto | supera il limite per trade | `block` o `warn` |
| rischio totale trader troppo alto | trader supera il proprio cap | `block` o `warn` |
| rischio totale globale troppo alto | il sistema supera il cap globale | `block` o `warn` |
| troppi segnali stesso simbolo | supera il numero massimo consentito | `block` o `warn` |
| prezzo fuori range | prezzo anomalo rispetto a limiti statici | `block` o `warn` |

### Lettura pratica

| Tipo controllo | Scopo |
|---|---|
| controllo segnale singolo | evitare un trade troppo rischioso |
| controllo per trader | evitare che un trader consumi troppo rischio |
| controllo globale | evitare che il sistema nel complesso rischi troppo |
| controllo simbolo | evitare sovraesposizione sullo stesso asset |
| controllo prezzi | evitare errori grossolani di parsing o digitazione |

---

## Cosa resta uguale rispetto alla Fase 4 attuale

Restano validi:

- i gate globali
- i limiti per trader
- il controllo sullo stesso simbolo
- la gestione di `entry_split`
- il `target resolver`
- la persistenza in `operational_signals`
- lo snapshot di `position_management`

Cambia soprattutto il significato dell'input principale:

- oggi il centro del sistema e' `position_size_pct`
- nel modello proposto il centro del sistema e' il rischio massimo accettato

---

## Output che la Fase 4 deve produrre

Per ogni segnale valido la Fase 4 dovrebbe produrre almeno:

- `is_blocked`
- `block_reason`
- `risk_mode`
- `risk_budget_usdt`
- `capital_base_usdt`
- `sl_distance_pct`
- `position_size_usdt`
- `position_size_pct` come dato derivato
- `leverage`
- `entry_split`
- `management_rules`
- `applied_rules`
- `warnings`

Per gli update con target:

- `resolved_target_ids`
- `target_eligibility`
- `target_reason`

---

## Decisioni da confermare

Prima di implementare conviene confermare questi punti:

- usare davvero `position_size_pct` solo come dato derivato e non come input
- supportare entrambe le modalita': percentuale capitale e valore fisso
- permettere override per singolo trader
- usare inizialmente `capital_base_mode = static_config`
- decidere se tenere un hard cap separato per il rischio singolo trade
- per segnali multi-entry usare media pesata se disponibile, altrimenti media semplice
- decidere se la distribuzione dei TP deve essere automatica o separata per 2 TP, 3 TP, 5 TP
- decidere se limitare globalmente i TP seguiti oppure seguire sempre tutti quelli del trader
- bloccare il segnale se non c'e' uno stop loss valido

---

## Decisioni gia' confermate

Queste sono le scelte emerse dalla revisione fatta fin qui.

| Tema | Scelta confermata |
|---|---|
| Modalita' rischio | supportare sia `risk_pct_of_capital` sia `risk_usdt_fixed` |
| `position_size_pct` | non deve essere input principale, solo dato derivato |
| Capitale base | statico da config adesso, equity live piu' avanti |
| Entry multiple | distribuzione manuale in config |
| Prezzo di riferimento rischio con piu' entry | media semplice |
| Gestione TP | regola globale con override per trader |
| Distribuzione chiusure sui TP | adattiva globale con override per trader |
| Stop loss mancante o non valido | blocco del segnale |
| Override per singolo trader | si |

### Tradotto in modo molto semplice

- io scelgo quanto posso perdere
- il sistema calcola la size
- se il segnale ha piu' entry, i pesi li definisco io in config
- se il segnale ha molti TP, posso gestirli con regola globale e correggere trader per trader
- se manca uno stop valido, il segnale non passa

### Punti ancora aperti

- decidere se tenere un hard cap separato sul rischio singolo trade
- decidere se limitare globalmente il numero di TP seguiti oppure seguire sempre tutti quelli del trader

---

## Sintesi finale

Il modello semplice e' questo:

- io decido quanto posso perdere
- il sistema guarda entry, stop loss e leva
- il sistema calcola la size giusta
- i limiti globali e per trader controllano che il rischio totale resti sotto controllo

Questo e' il comportamento piu' coerente se per "rischio" si intende davvero:

- perdita massima accettata
