# Design Spec - Signal Gate Per Tipo Messaggio

**Data:** 2026-06-22
**Argomento:** Gate operativo del `SIGNAL` basato sul tipo di presentazione del messaggio Telegram
**Documento operativo:** `docs/Raggionamento/2026-06-22_caso4_signal_gate_tipo_messaggio.md`

---

## Contesto

Su alcuni topic il trader pubblica prima un messaggio testuale normale, poi lo cancella e lo ripubblica come nuovo messaggio Telegram con bottoni inline.

Oggi il primo messaggio puo` essere accettato come `SIGNAL` operativo e creare subito una chain runtime. Quando gli update successivi si agganciano invece al secondo messaggio, la chain nasce sul messaggio sbagliato e il target resolution diventa incoerente.

Vincoli confermati:

- non rompere il flusso attuale;
- tutti i `raw_messages` devono restare persistiti;
- tutti i messaggi devono restare parsati, salvo esclusioni gia` esistenti;
- modifica minima;
- niente `clean_log` aggiuntivo per i mismatch, per evitare spam specifico del topic.

---

## Decisioni di design

### 1. Fonte Telegram nativa, persistenza normalizzata minimale

Il listener usa il dato nativo Telegram solo per osservare se il messaggio ha bottoni inline oppure no.

La persistenza non salva una struttura Telegram grezza e non espone dettagli del transport oltre il necessario. Il listener normalizza l'osservazione in un campo stabile di dominio:

`message_presentation_type`

Valori iniziali supportati:

- `PLAIN`
- `INLINE_BUTTONS`

Questo metadato viene propagato in:

- raw ingestion;
- `raw_messages`;
- `RawMessageEnvelope`;
- layer runtime che decide se il `SIGNAL` puo` diventare operativo.

### 2. Configurazione opzionale a livello canale/topic

`channels.yaml` aggiunge una policy opzionale per canale/topic:

- `signal_message_type: any`
- `signal_message_type: inline_buttons`

Semantica:

- se la chiave non e` presente, il comportamento resta invariato;
- `any` equivale al comportamento attuale;
- `inline_buttons` permette la nascita di una chain solo se il raw osservato ha `message_presentation_type=INLINE_BUTTONS`.

La regola resta nel resolver di configurazione topic/canale, non nel parser e non nel dominio del segnale canonico.

### 3. Il parser non cambia significato

Il parser continua a descrivere il contenuto del messaggio.

Quindi:

- un messaggio semanticamente `SIGNAL` continua a essere parsato come `SIGNAL`;
- raw e canonico restano persistiti anche se il topic richiede un tipo messaggio diverso;
- non vengono introdotti warning o classi parser dedicate a questa policy.

La separazione resta esplicita:

- parse = che cosa dice il messaggio;
- gate runtime = se quel `SIGNAL` puo` diventare operativo in quel topic.

### 4. Punto di blocco: path `SIGNAL` del lifecycle gate

Il controllo viene applicato dopo parse/enrichment e prima della creazione della chain, nel path `primary_class == "SIGNAL"` del `LifecycleEntryGate`.

Comportamento:

- policy `any` -> nessun cambiamento;
- policy `inline_buttons` + `INLINE_BUTTONS` -> il segnale segue il flusso normale;
- policy `inline_buttons` + `PLAIN` -> skip silenzioso.

Skip silenzioso significa:

- nessuna `TradeChain`;
- nessun `SIGNAL_ACCEPTED`;
- nessun `TRADE_CHAIN_CREATED`;
- nessun comando esecutivo;
- nessun `clean_log` dedicato.

### 5. Tracciabilita` interna del blocco

Anche se il blocco e` silenzioso verso l'utente, il runtime deve mantenere un motivo auditabile interno.

Reason code approvato:

- `signal_message_type_mismatch`

Il codice va registrato nel punto piu` vicino alla decisione runtime, senza introdurre un nuovo flusso di notifica. La priorita` e` rendere il caso debuggabile senza trasformarlo in rumore operativo.

---

## Flusso desiderato

Scenario con topic configurato `inline_buttons`:

1. arriva un primo messaggio `PLAIN`;
2. raw persistito con `message_presentation_type=PLAIN`;
3. parse persistito come `SIGNAL`;
4. il lifecycle gate valuta la policy del topic;
5. il segnale viene scartato in modo silenzioso con reason code interno `signal_message_type_mismatch`;
6. non nasce nessuna chain;
7. arriva il secondo messaggio con bottoni inline;
8. raw persistito con `message_presentation_type=INLINE_BUTTONS`;
9. parse persistito come `SIGNAL`;
10. il lifecycle gate consente il passaggio;
11. nasce la chain corretta sul secondo messaggio.

---

## Change Surface

Layer coinvolti:

- listener Telegram / intake: osservazione `reply_markup` inline e normalizzazione;
- persistenza parser DB: nuovo campo raw;
- `RawIngestItem` / `TelegramIncomingMessage` / `RawMessageEnvelope`;
- `RawMessageRepository` e mapping row -> envelope;
- `ChannelConfigResolver` e relativo modello `ChannelEntry`;
- `LifecycleEntryGate` nel solo path `SIGNAL`;
- test dei layer sopra.

Layer esplicitamente non coinvolti:

- parser semantico;
- traduzione canonica;
- regole di parse;
- `clean_log` formatter come nuova feature.

---

## Acceptance Contract

Done significa che il sistema distingue tra segnale semanticamente valido e segnale operativamente accettabile in base alla presentazione del messaggio, senza perdere raw o parse.

Criteri osservabili:

1. un topic senza `signal_message_type` continua a comportarsi come oggi;
2. un topic `inline_buttons` persiste e parsa un `SIGNAL` `PLAIN`, ma non crea chain;
3. lo stesso contenuto ripubblicato come nuovo messaggio con bottoni inline crea la chain;
4. il mismatch non genera `clean_log` o notifiche utente aggiuntive;
5. il runtime mantiene una traccia interna con reason code `signal_message_type_mismatch`.

Segnale primario:

- nessuna chain nasce sul primo messaggio `PLAIN` nei topic che richiedono bottoni inline.

Segnali secondari:

- test mirati verdi su listener/intake, config resolver, repository/envelope e lifecycle gate;
- assenza di eventi `SIGNAL_ACCEPTED` / `TRADE_CHAIN_CREATED` nel caso mismatch;
- non regressione del comportamento default.

---

## Validazione prevista

- test unit della normalizzazione `Telegram -> message_presentation_type`;
- test repository/envelope per il nuovo campo raw;
- test config resolver per `signal_message_type`;
- test `LifecycleEntryGate` per il caso `inline_buttons + PLAIN`;
- test di non regressione per il default `any`.

Non e` richiesto in questa fase introdurre una suite end-to-end piu` larga se i test mirati coprono il punto di ownership.

---

## Non obiettivi e limiti

- Non risolve il problema generale del target resolution in tutti i casi reply/link.
- Non cambia la semantica del parser.
- Non introduce nuove notifiche operative.
- Non sostituisce un eventuale hardening futuro del resolver sui repost/cancel pattern.

Questa fix e` deliberatamente minima e mirata al pattern:

`messaggio semplice -> cancellazione -> repost con bottoni inline`

---

## Raccomandazione finale

La soluzione approvata e`:

1. osservare nel listener la presenza di bottoni inline usando il dato nativo Telegram;
2. persistere solo un metadato normalizzato minimale `message_presentation_type`;
3. aggiungere una policy opzionale `signal_message_type` per topic/canale;
4. applicare il blocco solo nel `LifecycleEntryGate`, nel path `SIGNAL`;
5. rendere il blocco silenzioso verso l'utente ma auditabile internamente con `signal_message_type_mismatch`.
