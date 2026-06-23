# Promemoria — Caso 3: target resolution e chiusura sulla chain sbagliata

## Caso osservato

Pattern ricostruito:

1. arriva un signal;
2. il bot lo esegue e apre una posizione;
3. il messaggio viene poi eliminato oppure esiste un altro signal con riferimenti molto simili;
4. piu` avanti arriva un altro messaggio con stesso `Signal ID` o stesso `symbol`;
5. una posizione resta aperta, un altro signal viene rifiutato dalla policy;
6. arriva un update di chiusura;
7. il resolver chiude la chain aperta sbagliata, invece della posizione a cui l'update era destinato.

Questo non e` solo un problema di osservabilita`.

E` un problema di **corruzione operativa**:

- update applicato alla chain sbagliata;
- chiusura della posizione sbagliata;
- audit finale incoerente rispetto all'intenzione del trader.

---

## Gravita`

`Alta`

Motivo:

- l'errore non e` "update non applicato";
- l'errore e` "update applicato al target sbagliato".

Tra tutti i casi osservati, questo e` uno dei piu` pericolosi.

---

## Root cause strutturale

Il modello attuale puo` arrivare a questo esito quando:

1. la reference forte non e` persistita bene o non e` disponibile al resolver;
2. il reply non basta o viene perso lungo il percorso;
3. il resolver cade su match deboli;
4. in ultima istanza usa fallback come:
   - symbol
   - unica chain aperta del trader

In questo scenario, un update che doveva essere:

```text
no_update_target
```

oppure:

```text
ambiguous_update_target
```

diventa invece:

```text
apply to surviving open chain
```

ed e` proprio questo il comportamento da eliminare.

---

## Domanda chiave

Qual e` il metodo corretto di verifica per ridurre questo rischio?

La risposta e`:

- non una sola verifica;
- ma una verifica in piu` momenti, con priorita` diverse.

---

## Metodo di verifica proposto

### Livello 1 — verifica alla nascita della chain

Quando un signal viene accettato e crea una chain, il runtime deve validare e fissare subito la sua identita` forte.

Dati minimi da verificare/persistire:

- `raw_message_id`
- `telegram_message_id`
- `external_signal_id`
- `symbol`
- `side`
- `chat_id`
- `topic_id`
- `trader_id`

Scopo:

- capire subito se l'identita` del signal e` incompleta, debole o collisionata.

Questa verifica non impedisce da sola l'errore del Caso 3, ma prepara il terreno.

### Livello 2 — verifica al momento della risoluzione dell'update

Questa e` la verifica **principale**.

Se l'update contiene una reference forte, il resolver deve comportarsi cosi`:

- `0 match` -> `no_update_target`
- `1 match` -> applicabile
- `>1 match` -> `ambiguous_update_target`

Regola fondamentale:

se esiste una reference forte e quella reference **non risolve in modo univoco**, il resolver **non deve mai** scendere a fallback deboli.

Questa e` la protezione piu` importante per evitare la chiusura della chain sbagliata.

### Livello 3 — verifica post-apertura / periodica

Utile come audit e monitoraggio, non come difesa primaria.

Serve per trovare:

- chain aperte con `external_signal_id` nullo ma disponibile nei diagnostics;
- collisioni di identity forti;
- update accettati solo tramite fallback debole;
- drift tra reference dichiarata e target realmente colpito.

Questo check e` utile, ma arriva dopo. Non sostituisce il guardrail del Livello 2.

---

## Ruolo degli eventi `edit/delete`

Gli eventi di `edit/delete` possono aiutare, ma **non** devono essere trattati come chiave primaria di risoluzione.

### Cosa possono fare bene

#### 1. Aumentare il livello di rischio

Se il messaggio origine di una chain:

- viene editato in modo sostanziale;
- oppure viene cancellato;

la chain puo` essere marcata con uno stato logico tipo:

- `source_message_unstable`
- `source_message_deleted`
- `identity_needs_review`

Questo non chiude nulla automaticamente, ma dice al resolver:

```text
non fidarti dei fallback deboli su questa chain
```

#### 2. Restringere il comportamento del resolver

Se un update arriva con targeting incompleto e una chain candidata ha avuto `edit/delete` sull'origine, il sistema deve essere piu` severo:

- preferire `ambiguous_update_target`;
- preferire `no_update_target`;
- evitare fallback automatici.

#### 3. Aiutare a modellare supersede / replacement

Nel pattern:

- messaggio A
- delete/edit
- messaggio B

gli eventi di revisione possono aiutare a costruire una relazione:

- `A superseded by B`
- `A invalidated`

Questo e` utile nel redesign generale, ma non basta da solo per decidere il target.

### Cosa non devono fare

`edit/delete` non devono mai significare da soli:

```text
allora l'update appartiene sicuramente a questa chain
```

Perche':

- un delete non identifica da solo il sostituto corretto;
- un edit non garantisce continuita` operativa;
- il rischio di falsi match resta alto.

### Decisione raccomandata

Per il Caso 3:

- `edit/delete` = evidence secondaria, fattore di cautela;
- `explicit_id/reply/link` = evidence primaria;
- `symbol/unica chain aperta` = fallback debole, da vietare se una evidence primaria era presente ma fallita.

---

## Invarianti di sicurezza consigliate

### Invariante 1

Se l'update contiene una reference forte, il sistema non puo` applicarlo su una chain scelta solo per fallback debole.

### Invariante 2

Una reference forte che fallisce deve produrre:

- `no_update_target`, oppure
- `ambiguous_update_target`

mai:

- `best effort apply`

### Invariante 3

Una chain con sorgente `edited/deleted/unstable` non deve essere destinataria di update tramite soli indizi deboli.

### Invariante 4

Se due chain o due segnali condividono la stessa identity forte nello stesso perimetro logico, il sistema deve elevare il caso a collisione/ambiguita`, non scegliere arbitrariamente.

---

## Soluzione minima consigliata

Senza fare ancora il redesign completo:

1. rendere affidabile la persistenza delle identity forti;
2. introdurre un controllo esplicito nel resolver:
   - se evidence forte presente ma non risolta univocamente -> stop;
3. usare `edit/delete` come moltiplicatore di cautela;
4. vietare fallback deboli nei casi con reference forte fallita.

Questa e` la patch minima con il miglior rapporto rischio/beneficio.

---

## Soluzione strutturale

Nel redesign generale, il Caso 3 si risolve meglio passando a:

```text
reference -> signal_origin_id -> chain
```

In quel modello:

- `explicit_id`
- raw reply
- bot reply
- link
- superseded message

diventano tutte reference registrate e verificabili.

---

## Raccomandazione finale

Per il Caso 3, la misura piu` importante non e` una verifica solo "dopo apertura posizione".

La misura piu` importante e`:

- **verifica forte al momento della risoluzione update**

con queste regole:

- evidence forte fallita -> non applicare;
- `edit/delete` -> alzare severita`;
- niente fallback debole se il targeting forte era presente ma non affidabile.

Questo e` il guardrail che evita di chiudere la chain sbagliata.
