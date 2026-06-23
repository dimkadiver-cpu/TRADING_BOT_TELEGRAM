# Promemoria — Redesign generale del target resolution

## Contesto

L'analisi dei casi live mostra che il problema non e` solo "reply a messaggio del bot interno".

Il problema e` piu` generale:

- un update puo` riferirsi a un signal tramite piu` forme diverse;
- alcune reference sono forti, altre deboli;
- oggi il sistema combina piu` strategie di match con fallback;
- in presenza di mismatch o missing identity puo` finire a chiudere/applicare update sulla chain sbagliata.

Questo promemoria propone una direzione di redesign che copra piu` casi con un modello unico.

---

## Stato attuale sintetico

Oggi il runtime risolve i target usando una strategia multi-match con priorita`:

- scope globale;
- symbol;
- explicit ID;
- telegram message id / reply;
- fallback su unica chain aperta del trader.

Problemi strutturali:

1. il resolver ragiona troppo direttamente sulle chain aperte;
2. non esiste una identity canonica unica davvero affidabile in runtime;
3. alcune reference forti non vengono persistite o non vengono usate fino in fondo;
4. il fallback finale puo` essere pericoloso se una reference forte ha fallito;
5. reply a messaggi del bot interno non appartengono naturalmente al modello attuale.

---

## Obiettivo del redesign

Passare da:

```text
hint update -> cerca di indovinare la chain
```

a:

```text
reference -> identity canonica del signal -> chain corretta
```

Questa e` la differenza fondamentale.

---

## Idea chiave

Introdurre una identity logica canonica del signal, separata dagli ID tecnici attuali.

Esempi di ID oggi esistenti ma non sufficienti da soli:

- `raw_message_id`
- `telegram_message_id`
- `canonical_message_id`
- `enrichment_id`
- `trade_chain_id`
- `external_signal_id`

Serve un nuovo concetto:

- `signal_origin_id`

`signal_origin_id` rappresenta l'identita` stabile della "idea operativa" che ha generato o puo` generare una chain.

---

## Modello concettuale proposto

### 1. Message observed

E` il messaggio Telegram visto dal listener.

Contiene:

- raw text;
- chat/topic;
- `telegram_message_id`;
- metadata osservati;
- eventuale `message_type`;
- eventuali bottoni;
- eventuale delete/edit/supersede history.

Questo livello non decide ancora se il messaggio diventa operativo.

### 2. Signal accepted

E` il momento in cui il runtime accetta un `SIGNAL` e crea la chain.

Qui nasce:

- `signal_origin_id`

Questo e` il punto in cui il sistema decide quale messaggio osservato e` la vera origine operativa.

### 3. Reference registry

Qualsiasi cosa in futuro possa puntare a quel signal va salvata come reference esplicita.

Esempi:

- `RAW_TELEGRAM_MESSAGE`
- `RAW_REPLY_MESSAGE`
- `EXTERNAL_SIGNAL_ID`
- `TELEGRAM_LINK`
- `BOT_MESSAGE_ID`
- `BOT_ROOT_MESSAGE_ID`
- `SUPERSEDED_MESSAGE_ID`

Tutte queste reference devono convergere verso:

- `signal_origin_id`

### 4. Chain binding

Una volta trovato `signal_origin_id`, il runtime trova la chain operativa attiva collegata.

Quindi:

- il resolver non parte dalle chain;
- il resolver parte dalle reference.

---

## Tabella concettuale da introdurre

Non e` ancora uno schema finale, ma il modello dovrebbe assomigliare a questo:

```text
signal_reference_registry
  reference_id
  signal_origin_id
  reference_kind
  reference_value
  source_chat_id
  source_topic_id
  trader_id
  account_id
  confidence
  status
  created_at
  updated_at
```

Campi importanti:

- `reference_kind`
  esempi: `RAW_MESSAGE_ID`, `TELEGRAM_MESSAGE_ID`, `EXTERNAL_SIGNAL_ID`, `BOT_MESSAGE_ID`

- `reference_value`
  valore normalizzato della reference

- `status`
  esempi:
  - `ACTIVE`
  - `SUPERSEDED`
  - `REJECTED`
  - `AMBIGUOUS`

- `confidence`
  utile se in futuro si distinguono match hard vs soft

---

## Nuovo flusso di risoluzione

### Oggi

```text
update -> target hints -> match diretto su chain aperte
```

### Proposto

```text
update
 -> estrai e normalizza tutte le reference candidate
 -> consulta il registry
 -> ottieni 0 / 1 / N signal_origin_id
 -> se 1: trova la chain attiva corretta
 -> se 0: no_update_target
 -> se N: ambiguous_update_target
```

Questo rende il sistema piu` spiegabile e piu` sicuro.

---

## Regole di priorita` proposte

### Reference forti

Da trattare come hard evidence:

- `external_signal_id`
- reply a messaggio raw noto
- reply a messaggio bot noto
- telegram link che punta a un messaggio registrato

### Reference deboli

Da usare solo se non esistono reference forti:

- symbol
- side
- scope globale
- unica chain aperta del trader

### Regola fondamentale

Se un update contiene una **reference forte** e quella reference:

- non matcha nulla -> `no_update_target`
- matcha piu` origin -> `ambiguous_update_target`

In questo caso **non** si deve fare fallback a symbol o unica chain aperta.

Questa regola chiude il bug piu` pericoloso osservato.

---

## Gestione messaggi sostituiti / ripubblicati

Caso tipico:

1. compare messaggio A;
2. viene cancellato;
3. compare messaggio B con nuovo `telegram_message_id`;
4. dal punto di vista umano e` "lo stesso signal";
5. dal punto di vista Telegram e` un messaggio nuovo.

Il redesign deve poter rappresentare esplicitamente queste relazioni:

- `message A superseded by message B`
- `message A observed but non-operational`
- `message B accepted as signal origin`

Questo permette di non perdere audit e al tempo stesso di evitare che la chain nasca sul messaggio sbagliato.

---

## Gestione messaggi del bot interno

I messaggi del bot interno devono diventare reference first-class.

Ogni messaggio inviato dal control plane e collegato a una chain dovrebbe poter registrare:

- `BOT_MESSAGE_ID -> signal_origin_id`

e, se utile:

- `BOT_ROOT_MESSAGE_ID -> signal_origin_id`
- `BOT_LAST_MESSAGE_ID -> signal_origin_id`

Cosi` un reply dell'utente a un messaggio del bot non e` piu` un caso speciale:

- e` semplicemente una reference registrata.

---

## Separazione delle responsabilita`

### Parser

Responsabilita`:

- capire semanticamente cosa dice il messaggio;
- estrarre intent, explicit id, reply info, links, symbols.

Non deve:

- decidere policy operative di topic;
- scegliere la chain finale;
- fare gating architetturale sul tipo di UI del messaggio.

### Runtime / lifecycle / resolver

Responsabilita`:

- applicare policy operative;
- risolvere target in modo deterministico;
- rifiutare update non risolvibili o ambigui;
- evitare fallback pericolosi.

---

## Benefici attesi

1. meno dipendenza da fallback impliciti;
2. piu` auditabilita` delle decisioni;
3. un solo modello per raw reply, explicit id, bot reply, repost, link;
4. minore rischio di chiudere o modificare la chain sbagliata;
5. possibilità di rollout graduale senza riscrivere tutto in una volta.

---

## Tradeoff

1. e` un redesign reale, non una patch minima;
2. richiede nuova persistenza;
3. richiede migrazione graduale del resolver;
4. va pensato bene il backfill delle reference per le chain gia` esistenti.

---

## Strategia di rollout consigliata

### Fase 1

Patch minima sul Caso 4:

- persistere `message_type`;
- gate signal per topic;
- nessuna modifica profonda al resolver.

### Fase 2

Hardening identity:

- garantire persistenza affidabile di `external_signal_id`;
- registrare reference base dei nuovi signal accettati.

### Fase 3

Introduzione `signal_origin_id`:

- ogni nuovo signal accettato riceve identity canonica;
- registrazione reference primaria.

### Fase 4

Registry-driven resolution:

- update risolti prima sul registry;
- fallback forte/debole rivisto.

### Fase 5

Bot message references:

- reply a messaggi del bot interno supportati come reference native.

### Fase 6

Riduzione fallback legacy:

- eliminare i fallback non sicuri nei casi con evidence forte.

---

## Decisione raccomandata

Non fare un fix locale sul reply al bot interno come caso isolato.

La direzione consigliata e`:

- accettare una patch minima immediata sul Caso 4;
- progettare da subito il passaggio a `signal_origin_id + reference registry`;
- migrare il target resolution verso un modello identity-first.

Questa e` la strada che copre piu` casi senza moltiplicare eccezioni ad hoc.

---

## Collegamento rapido con i casi osservati

### Caso 1 — edit del signal gia` eseguito con cambio `Signal ID`

Pattern:

- nasce la chain sul primo messaggio;
- arriva un edit del messaggio;
- cambia l'explicit ID del signal;
- il listener registra la revisione ma non riprocessa il signal.

Gravita`:

- `media` come mismatch di identita`/audit;
- puo` diventare `alta` se poi arrivano update che puntano al nuovo ID ma la chain conosce solo il vecchio.

Direzione di soluzione:

- mantenere la regola "non rieseguire edit di signal gia` eseguito";
- ma registrare le nuove identity references generate dall'edit;
- trattare il nuovo explicit ID come reference aggiuntiva o superseding, non come nuovo signal autonomo.

### Caso 2 — edit postumo che aggiunge un report

Pattern:

- il signal e` gia` stato eseguito;
- l'edit aggiunge solo informazione di tipo report/esito;
- la chain non deve cambiare.

Gravita`:

- `bassa`.

Direzione di soluzione:

- nessun reprocess operativo;
- eventuale sola osservabilita`/audit della revisione;
- nessun impatto diretto sul target resolution.

### Caso 3 — piu` segnali / chain con riferimenti confliggenti e chiusura sulla chain sbagliata

Pattern:

- reference forte presente ma non risolta in modo affidabile;
- fallback verso symbol / unica chain aperta;
- update applicato alla chain sbagliata.

Gravita`:

- `alta`.

Questo e` il caso che dimostra che il modello attuale puo` produrre corruzione operativa, non solo mancata risoluzione.

Direzione di soluzione minima:

- se un update contiene una reference forte e quella reference fallisce, non fare fallback debole;
- restituire `no_update_target` o `ambiguous_update_target`;
- rendere affidabile la persistenza delle identity forti (`external_signal_id`, reply, link).

Direzione strutturale:

- passare al modello `reference -> signal_origin_id -> chain`.

### Caso 4 — signal ripubblicato con nuovo messaggio e bottoni

Pattern:

- primo messaggio semplice visto e processato troppo presto;
- messaggio cancellato;
- nuovo messaggio con bottoni pubblicato come nuovo `telegram_message_id`;
- update futuri si allineano al secondo messaggio, ma la chain e` nata dal primo.

Gravita`:

- `medio-alta` come problema locale;
- e` il segnale che il sistema deve distinguere meglio tra "messaggio osservato" e "signal operativo valido".

Direzione di soluzione minima:

- persistenza `message_type`;
- gate operativo per topic;
- nessun blocco nel parser.

Direzione strutturale:

- integrare anche questo caso nel registry delle reference e nella gestione di messaggi superseded / ripubblicati.

---

## Ordine pragmatico consigliato

### Priorita` 1

Caso 4:

- gate signal per tipo messaggio su topic;
- blocco della nascita prematura della chain sbagliata.

### Priorita` 2

Caso 3:

- eliminare i fallback pericolosi quando esiste evidence forte fallita;
- preferire `no_update_target` / `ambiguous_update_target` a un match sbagliato.

### Priorita` 3

Caso 1:

- registrare meglio le identity references sugli edit di signal gia` eseguiti.

### Priorita` 4

Caso 2:

- solo audit / osservabilita`, senza impatto sul flusso operativo.
