# Caso 4 — Gate del segnale per tipo messaggio

## Problema osservato

Su alcuni canali/topic il flusso reale e`:

1. compare un messaggio signal "normale";
2. il bot lo acquisisce e lo processa subito come `SIGNAL`;
3. pochi secondi dopo il messaggio viene cancellato;
4. lo stesso contenuto viene ripubblicato come nuovo messaggio Telegram, con nuovo `telegram_message_id`;
5. il nuovo messaggio presenta bottoni cliccabili sotto al post;
6. update e reply successivi si agganciano al secondo messaggio, mentre la chain runtime e` nata sul primo.

Effetto: il primo `SIGNAL` rompe la coerenza del target resolution per gli update successivi.

---

## Vincoli decisi

- Non rompere il flusso attuale.
- Tutti i `raw_messages` devono restare persistiti.
- Tutti i messaggi devono restare parsati, salvo esclusioni gia` esistenti.
- La modifica deve essere minima.
- Serve persistere un attributo di tipo messaggio osservato.
- Serve impedire che un messaggio parsato come `SIGNAL` diventi operativo se il topic richiede un tipo diverso.

---

## Decisione di configurazione

Per ora la regola e` a livello `canale/topic`.

Semantica desiderata:

- se il topic **non** ha regola specifica: comportamento invariato;
- se il topic ha regola attiva: un messaggio puo` diventare `SIGNAL` operativo solo se ha il tipo richiesto.

Direzione minima proposta in config:

```yaml
signal_message_type: ANY
```

oppure

```yaml
signal_message_type: INLINE_BUTTONS_ONLY
```

`INLINE_BUTTONS_ONLY` significa:

- il raw viene acquisito;
- il parser produce comunque il canonico;
- ma se il canonico e` `SIGNAL` e il messaggio non ha bottoni inline, il signal non deve proseguire come signal operativo.

---

## Persistenza minima richiesta

Serve aggiungere al `raw_message` un attributo osservato dal listener Telegram, ad esempio:

- `PLAIN`
- `INLINE_BUTTONS`

Questa informazione deve essere disponibile almeno fino al punto in cui il runtime decide se accettare il signal.

Non serve cambiare il significato del parser; serve solo rendere disponibile il metadato di presentazione del messaggio.

---

## Punto corretto del blocco

### Non nel parser

Bloccare nel parser e` il punto sbagliato.

Motivi:

1. Il parser deve descrivere il contenuto semantico del messaggio, non applicare policy Telegram-specifiche.
2. La regola dipende da configurazione `chat/topic`, quindi appartiene a un layer runtime/policy, non al parser canonico.
3. Se il parser iniziasse a sopprimere `SIGNAL` in base al tipo UI del messaggio, il risultato del parse diventerebbe dipendente da metadata di trasporto e dalla config di deployment.
4. Il vincolo utente e` conservare il parse: un blocco nel parser rischia di confondere "messaggio semanticamente e` un signal" con "messaggio operativamente non e` accettabile".

In sintesi:

- **parse** = "che cosa dice il messaggio";
- **gate operativo** = "questo signal puo` generare una chain in questo topic".

Sono due responsabilita` diverse.

### Punto giusto

Il blocco va applicato **dopo il parse** e **prima della creazione della chain**.

I due punti ragionevoli sono:

1. `signal_enrichment`
2. `lifecycle gate` del path `SIGNAL`

### Raccomandazione

Il punto piu` coerente e minimale e` il **gate signal** del runtime, prima di `SIGNAL_ACCEPTED` e `TRADE_CHAIN_CREATED`.

Motivi:

- e` il layer che decide gia` oggi se un signal e` accettabile o no;
- non altera raw ingestion;
- non altera il parser;
- non richiede di reinterpretare il canonico;
- limita la modifica al momento in cui nasce la chain, che e` il vero problema da fermare.

---

## Comportamento desiderato

Scenario con topic configurato come `INLINE_BUTTONS_ONLY`:

1. arriva il primo messaggio semplice;
2. raw persistito;
3. parse persistito come `SIGNAL`;
4. il runtime verifica il tipo messaggio del raw;
5. il runtime **non accetta** il signal come operativo;
6. nessuna chain viene creata;
7. arriva il secondo messaggio con bottoni;
8. raw persistito;
9. parse persistito come `SIGNAL`;
10. il runtime verifica il tipo messaggio;
11. il signal viene accettato;
12. nasce la chain corretta sul secondo messaggio.

Questo preserva audit e replay, ma impedisce la creazione anticipata della chain sbagliata.

---

## Modalita` del blocco

Per questo caso il blocco puo` essere trattato come **silenzioso dal punto di vista del parser**, ma non dovrebbe essere invisibile nel runtime.

Interpretazione corretta:

- non serve sporcare il parser con warning o classi diverse;
- ma il runtime deve poter motivare internamente perche' il signal non e` stato accettato.

Quindi il blocco deve essere:

- silenzioso nel parse;
- esplicito nel layer operativo, con reason code dedicato.

Esempio concettuale:

```text
reason_code = signal_message_type_mismatch
```

Questo evita ambiguita` future durante audit e debugging.

---

## Soluzione a due livelli

### Livello 1 — osservazione/persistenza

- rilevare se il messaggio Telegram ha inline buttons;
- persistere il tipo messaggio insieme al raw.

### Livello 2 — gate operativo signal

- leggere la regola del topic;
- se il topic richiede `INLINE_BUTTONS_ONLY` e il raw non ha quel tipo:
  - non creare chain;
  - non eseguire il signal;
  - lasciare invariati raw e parse.

---

## Tradeoff

### Vantaggi

- modifica minima;
- nessuna perdita di dati raw;
- nessuna perdita di dati parse;
- nessuna contaminazione del parser con policy di trasporto;
- impedisce il bug nel punto reale in cui fa danno: la nascita della chain.

### Limiti

- non risolve da solo il problema generale del target resolution via reply a messaggi del bot interno;
- copre bene il pattern "messaggio semplice -> cancellazione -> nuovo messaggio con bottoni", ma non sostituisce una futura hardening del resolver.

---

## Raccomandazione finale

Per il Caso 4, la soluzione minima corretta e`:

1. aggiungere in persistenza raw un metadato `message_type`;
2. introdurre config per topic `signal_message_type`;
3. applicare il blocco **dopo il parse** e **prima della creazione chain**;
4. non mettere questa logica nel parser.

Questa e` la soluzione con il miglior rapporto tra:

- impatto minimo;
- coerenza architetturale;
- riduzione del rischio operativo.
