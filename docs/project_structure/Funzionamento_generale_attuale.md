# Riassunto del funzionamento del bot — `TRADING_BOT_TELEGRAM`

## Architettura

```
Telegram channels
      ↓
Listener (Telethon)
      ↓
raw_messages (SQLite) — processing_status: pending → done | failed | blacklisted | review
      ↓
Router / Pre-parser
      ↓
Parser — RulesEngine + profile.py per trader → TraderParseResult (Pydantic)
      ↓
parse_results (SQLite)
      ↓
Validazione coerenza → Operation rules → Target resolver
      ↓
Sistema 1 (freqtrade live) + gost trader per testare si demo caount


## 1. Scopo generale

Questo bot serve a leggere messaggi Telegram da canali o gruppi configurati, salvarli nel database, interpretarli come segnali o aggiornamenti di trading, e trasformarli in dati strutturati utilizzabili dal sistema.

In pratica, il bot fa da ponte tra:

- **Telegram**
- **parser**
- **regole operative**
- **stato dei segnali/trade**
- eventuale **motore esecutivo esterno**

---

## 2. Flusso generale

Il flusso logico è questo:

1. il bot si collega a Telegram;
2. ascolta i nuovi messaggi dalle sorgenti abilitate;
3. salva subito il messaggio raw nel database;
4. mette il messaggio in coda interna;
5. un worker lo prende e lo analizza;
6. risolve il trader effettivo e verifica se il messaggio è utilizzabile;
7. prova a classificare il messaggio:
   - nuovo segnale
   - update
   - info
   - setup incompleto
   - non classificato
8. salva il risultato del parsing;
9. se il parsing è valido, applica le regole operative;
10. aggiorna le tabelle dei segnali operativi e dello stato.

---

## 3. Ingresso: da dove arrivano i messaggi

Le sorgenti Telegram vengono definite nella configurazione.

Per ogni sorgente si possono definire, in modo generale:

- identificativo chat/canale;
- label;
- stato attivo/non attivo;
- trader associato, se noto;
- blacklist locale.

Esiste anche una blacklist globale.

Questa configurazione viene riletta dal bot senza dover riavviare il processo.

---

## 4. Listener Telegram

Il listener è il componente che riceve i messaggi da Telegram in tempo reale.

Quando arriva un messaggio:

- controlla se la chat è ammessa;
- scarta i messaggi non utili, ad esempio certi media-only;
- costruisce un oggetto interno con i dati base del messaggio;
- salva il raw nel database;
- accoda il messaggio per l’elaborazione successiva.

Questa parte è pensata per essere veloce: prima acquisisce e persiste, poi delega l’analisi.

---

## 5. Persistenza raw

Ogni messaggio utile viene salvato come record raw.

A livello concettuale, nel raw vengono conservati:

- chat di origine;
- id del messaggio Telegram;
- eventuale reply al messaggio precedente;
- testo originale;
- timestamp;
- stato di acquisizione;
- eventuali dati media, se previsti.

Questa persistenza serve per:

- non perdere messaggi;
- fare recovery al riavvio;
- ricostruire catene e reply;
- rieseguire analisi in futuro.

---

## 6. Coda interna e worker

Dopo il salvataggio raw, il messaggio entra in una coda interna.

Un worker separato legge questa coda e processa i messaggi uno per volta.

Questo approccio separa due fasi:

- **acquisizione veloce**
- **analisi più lenta**

Così il listener non resta bloccato dal parsing o dalla logica operativa.

---

## 7. Risoluzione del trader e verifica di eleggibilità

Prima di interpretare il testo, il bot cerca di capire a quale trader appartiene il messaggio.

Può usare:

- mapping della sorgente;
- configurazione del canale;
- informazioni già presenti nel database;
- contesto del reply.

Poi verifica se il messaggio è eleggibile, cioè se ha senso processarlo come elemento della pipeline.

Se il trader non è risolvibile o il messaggio è dubbio, può finire in review invece che nel flusso operativo normale.

---

## 8. Parsing del messaggio

Il parser cerca di capire che tipo di messaggio è e cosa contiene.

Le classi principali sono, in sintesi:

- **NEW_SIGNAL**
- **UPDATE**
- **INFO_ONLY**
- **SETUP_INCOMPLETE**
- **UNCLASSIFIED**

Dal testo il parser prova a estrarre dati come:

- simbolo;
- direzione long/short;
- entry;
- stop loss;
- take profit;
- riferimenti a segnali precedenti;
- azioni implicite o esplicite.

Il risultato viene normalizzato in una struttura coerente.

---

## 9. Linking e contesto

Molti messaggi Telegram non sono segnali completi isolati, ma aggiornamenti riferiti a qualcosa di già esistente.

Per questo il bot prova a collegare i messaggi tramite:

- `reply_to_message_id`;
- link Telegram presenti nel testo;
- riferimenti interni al segnale;
- contesto della stessa sorgente.

Questo passaggio è molto importante soprattutto per gli update.

---

## 10. Salvataggio del parse result

Dopo il parsing, il bot salva un record strutturato con:

- tipo messaggio;
- trader risolto;
- esito di eleggibilità;
- dati estratti;
- warning;
- versione normalizzata del risultato.

Questo serve a separare chiaramente:

- **raw originale**
- **interpretazione strutturata**

---

## 11. Regole operative

Se il messaggio è valido e abbastanza completo, entra nella parte operativa.

Qui il sistema applica regole che decidono, ad esempio:

- se il segnale è bloccato o accettabile;
- quale risk mode usare;
- come derivare la size;
- quali avvisi produrre;
- come trasformare il parsing in un segnale operativo.

In questa fase non si tratta più solo di “capire il testo”, ma di trasformarlo in qualcosa di utilizzabile dal sistema di trading.

---

## 12. Target resolution per gli update

Se il messaggio è un update, il bot deve capire **a quale segnale/posizione si riferisce**.

Per questo usa un target resolver che prova a identificare il bersaglio corretto.

Se il target non è risolvibile:

- il messaggio può restare salvato;
- ma viene marcato come non risolto;
- eventualmente va in review.

---

## 13. Tabelle operative

Quando tutto va bene, il sistema alimenta anche tabelle più operative, ad esempio:

- segnali iniziali;
- segnali operativi;
- stato e avanzamento dei messaggi;
- eventuali aggiornamenti applicati.

In altre parole, il bot non si limita a leggere Telegram: costruisce una base dati coerente su cui poi si può lavorare.

---

## 14. Recovery al riavvio

Quando il bot riparte, non ricomincia “da zero”.

Fa due cose principali:

1. riprende eventuali messaggi rimasti a metà lavorazione;
2. chiede a Telegram i messaggi recenti mancanti entro una certa finestra temporale.

Questo riduce il rischio di perdere segnali durante stop, crash o riavvii.

---

## 15. Stato attuale, in parole semplici

In sintesi, oggi il bot funziona come una pipeline a strati:

- **Layer 1** → acquisizione Telegram
- **Layer 2** → salvataggio raw
- **Layer 3** → parsing e classificazione
- **Layer 4** → regole operative e targeting
- **Layer 5** → aggiornamento dello stato operativo

Quindi il bot non è solo un “listener Telegram”, ma un sistema che:

- ascolta,
- salva,
- interpreta,
- collega,
- normalizza,
- e prepara i dati per l’uso operativo.

---

## 16. Riassunto ultra-breve

Versione molto corta:

- legge messaggi Telegram dalle sorgenti configurate;
- salva ogni messaggio raw nel DB;
- lo mette in coda;
- un worker lo analizza e identifica trader, tipo messaggio e contenuto;
- salva il risultato strutturato;
- se valido, applica regole operative e aggiorna i segnali/stati.

