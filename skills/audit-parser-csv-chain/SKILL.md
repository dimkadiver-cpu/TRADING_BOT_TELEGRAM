---
name: audit-parser-csv-chain
summary: Audit ad alta precisione dei risultati parser partendo da CSV/report, ricostruendo la chain dei messaggi, confrontando parse salvato vs parse attuale e verificando anche la fedeltà delle entità estratte.
---

# Audit Parser CSV Chain

## Scopo

Usa questa skill quando devi fare audit dei risultati del parser **partendo dai CSV o dai report di `parser_test`**, non dal codice.

L'obiettivo è verificare se un parse riportato o salvato è coerente con:
- il testo raw del messaggio
- il parent diretto
- l'eventuale chain `reply -> reply -> signal`
- link target presenti nel testo
- scope globale implicito nel testo
- comandi precedenti rilevanti nello stesso thread
- entità estratte, inclusi numeri, prezzi, stop, TP, percentuali, leverage, quantità e ordine dei livelli

Questa skill serve a distinguere con precisione se un caso è:
- un bug reale del parser
- un problema di targeting
- un record DB stale
- un errore di export/report
- un falso allarme dovuto a testo ambiguo ma parse corretto

## Quando usarla

Usa questa skill quando:
- un CSV mostra `message_type`, `intents`, `entities` o `actions_structured` sospetti
- ci sono casi `UNRESOLVED`, `UNCLASSIFIED`, `SETUP_INCOMPLETE` inattesi
- un update breve sembra comprensibile solo guardando la chain reale della chat
- un parse sembra semanticamente giusto ma le entità numeriche sono sospette
- vuoi capire se il problema è parser, resolver, targeting, entity extraction o report export
- vuoi verificare se il record salvato in DB non è più allineato al parser attuale

## Input attesi

La skill lavora con uno o più dei seguenti input:
- uno o più CSV sotto `parser_test/reports/`
- uno o più `raw_message_id`
- opzionalmente un trader target
- opzionalmente un range ristretto di casi già sospetti

## Fonti da usare

Usa queste fonti in quest'ordine:

1. CSV sotto `parser_test/reports/`
2. DB test `parser_test/db/parser_test.sqlite3`
3. tabella `raw_messages`
4. tabella `parse_results`
5. parser attuale del trader, **solo se serve** verificare se il record DB è stale rispetto alla logica corrente

## Principio operativo

Questa skill fa audit del **risultato**, non del codice.

Il parser attuale va consultato solo quando serve distinguere:
- `stale_db_result`
- regressione/non regressione del parser attuale
- differenza tra parse storico salvato e parse ricalcolato oggi

Non partire mai dal codice se il problema può essere diagnosticato già da CSV + DB + chain.

---

## Workflow operativo

### 1. Individua i casi problematici nel CSV

Per ogni riga sospetta estrai almeno:
- `raw_message_id`
- `telegram_message_id`
- `message_type`
- `intents`
- `raw_text`
- eventuali campi `entities` / `actions_structured` / `warnings`

Se i CSV sono multipli, raggruppa i casi per:
- trader
- tipo messaggio
- severità apparente

### 2. Recupera il record reale dal DB

Per ogni caso, recupera da `raw_messages` e `parse_results` almeno:
- `raw_message_id`
- `source_chat_id`
- `telegram_message_id`
- `reply_to_message_id`
- `trader_code` o trader effettivo
- `raw_text`
- parse salvato completo
- eventuale stato di acquisition / resolution, se disponibile

### 3. Ricostruisci la chain dei messaggi

Ricostruisci sempre il contesto in questo ordine:
- messaggio corrente
- parent diretto da `reply_to_message_id`, se presente
- ulteriori parent successivi fino al root signal, se necessari
- eventuali target forti tramite link `t.me/.../<id>` o riferimenti espliciti a messaggi
- eventuali comandi precedenti rilevanti nello stesso thread

### 4. Determina il significato umano del messaggio

Valuta il testo in contesto reale e determina almeno:
- se è `NEW_SIGNAL`, `UPDATE`, `INFO_ONLY`, `SETUP_INCOMPLETE` o `UNCLASSIFIED`
- se esprime un'azione reale o solo un commento/informazione
- se il target è singolo, multiplo, globale o ambiguo
- se i riferimenti al target sono forti o deboli
- se le entità numeriche sono esplicite o implicite

### 5. Confronta parse salvato e contesto reale

Confronta il significato umano con il parse salvato nel DB.

Verifica almeno:
- classificazione del `message_type`
- correttezza degli `intents`
- coerenza di `actions_structured`
- correttezza del targeting
- coerenza con la chain reale
- completezza e fedeltà delle entità estratte

### 6. Se serve, confronta anche con il parser attuale

Ricalcola o verifica il parse attuale **solo se necessario** per capire se:
- il DB contiene un risultato vecchio o stale
- il parser attuale corregge già il problema
- il problema è nel report/export e non nel parser

### 7. Classifica il caso

Assegna il caso a una delle categorie definite sotto.

---

## Regole di ricostruzione chain

### Regole base

- partire sempre da `reply_to_message_id` se presente
- cercare il parent in `raw_messages` nello stesso `source_chat_id`
- se il parent diretto non basta, risalire altri hop
- se ci sono link `t.me/.../<id>`, considerarli target forti
- distinguere sempre:
  - segnale originario
  - update operativi successivi
  - outcome finali / risultati
  - commenti informativi non operativi

### Quando non fermarsi al parent diretto

Non fermarti al parent diretto se:
- il parent è a sua volta un update che dipende da un segnale precedente
- il messaggio attuale ha senso solo rispetto al root signal
- il parent non contiene abbastanza contesto per interpretare target o scope
- il testo usa formule tipo “qui”, “anche questo”, “sposto”, “chiudo tutto”, “убрал доливку”, “переносим”, “этот” e simili

### Link e target forti

Considera come target forti:
- reply dirette a un messaggio operativo
- link Telegram espliciti a messaggi
- riferimenti espliciti a `msg`, `message`, `ref`, `id`, `#123` se chiaramente collegabili

### Scope globale implicito

Riconosci come scope globale implicito casi tipo:
- “chiudo tutto”
- “tutte le posizioni”
- “all positions”
- riferimenti globali a longs/shorts/tutto il portafoglio

Se il testo indica scope globale, non forzare un singolo target solo perché esiste un parent diretto.

---

## Controllo entità estratte

Questa skill deve fare audit esplicito anche delle entità estratte.

Non basta verificare classificazione e intent. Devi controllare se i valori estratti sono **fedeli al testo raw**.

### Entità da controllare

Controlla almeno, quando presenti:
- simbolo / strumento
- side
- entry prices
- ordine delle entry
- ruolo delle entry (`PRIMARY`, `AVERAGING`)
- `stop_loss`
- `take_profits`
- `new_stop_level`
- percentuali
- leverage
- quantità
- invalidation
- livelli numerici citati in `actions_structured`

### Controlli obbligatori

Per ogni entità numerica verifica:
- **completezza**: il valore c'è o manca?
- **precisione**: il numero è stato preservato correttamente?
- **ordine**: i livelli sono nello stesso ordine del testo?
- **coerenza semantica**: il valore è assegnato al campo giusto?
- **normalizzazione**: il parser ha normalizzato bene separatori, spazi, decimali?

### Esempi di bug da segnalare

Segnala bug quando trovi casi come:
- `90 000.0` nel testo ma `90` nel parse
- `90,000` interpretato come `90.000`
- TP1 e TP2 invertiti
- entry A/B invertite
- una percentuale estratta come prezzo
- un prezzo numerico sostituito con `ENTRY` o altro placeholder senza giustificazione semantica
- perdita di ordini di grandezza
- estrazione parziale del numero
- valore inventato o non supportato dal testo

### Cosa confrontare

Confronta i valori presenti nel testo con quelli in:
- `entities`
- `entry_plan`
- `risk_plan`
- `instrument_obj`
- `position_obj`
- `actions_structured`

Se il testo contiene chiaramente un valore e il parse lo perde, lo tronca, lo inverte o lo sposta nel campo sbagliato, segnalo come bug parser sulle entità.

---

## Categorie di esito audit

Usa una sola categoria principale per caso, scegliendo la più precisa.

### `stale_db_result`
Usa questa categoria quando:
- il parse salvato nel DB non coincide con quello che produrrebbe il parser attuale
- la chain indica che il record è storico e non allineato alla logica corrente

### `parser_classification_bug`
Usa questa categoria quando:
- il `message_type` è sbagliato rispetto al significato reale del testo in contesto

### `parser_intent_bug`
Usa questa categoria quando:
- il `message_type` è plausibile
- ma gli `intents` sono sbagliati, incompleti o semanticamente scorretti

### `parser_entity_extraction_bug`
Usa questa categoria quando:
- il significato generale è giusto
- ma le entità estratte sono sbagliate, mancanti, invertite o assegnate al campo sbagliato

### `parser_numeric_normalization_bug`
Usa questa categoria quando:
- il problema principale riguarda parsing/normalizzazione numerica
- il numero perde ordini di grandezza, decimali o separatori
- il valore numerico non conserva fedeltà rispetto al raw text

### `targeting_bug`
Usa questa categoria quando:
- gli intent sono corretti
- ma il target è sbagliato, incompleto, troppo stretto o troppo ampio

### `trader_resolution_bug`
Usa questa categoria quando:
- il messaggio è stato attribuito al trader sbagliato
- oppure la risoluzione trader rende il parse incoerente

### `report_export_bug`
Usa questa categoria quando:
- il parse nel DB è corretto
- ma il CSV/report mostra valori sbagliati, mancanti o trasformati male

### `not_a_bug`
Usa questa categoria quando:
- il parse è coerente col testo e con la chain
- il caso era ambiguo ma il risultato attuale è accettabile

### `manual_review`
Usa questa categoria solo quando:
- anche dopo ricostruzione chain e confronto DB non c'è abbastanza contesto per decidere con affidabilità

---

## Distinzioni importanti

### `parser_intent_bug` vs `targeting_bug`
- `parser_intent_bug`: l'azione semantica è sbagliata
- `targeting_bug`: l'azione è giusta ma punta al target sbagliato

### `parser_entity_extraction_bug` vs `parser_numeric_normalization_bug`
- `parser_entity_extraction_bug`: campo sbagliato, entità mancante, livelli invertiti, valore assegnato male
- `parser_numeric_normalization_bug`: il numero stesso è stato letto/normalizzato male

### `stale_db_result` vs `report_export_bug`
- `stale_db_result`: il DB è vecchio rispetto al parser attuale
- `report_export_bug`: il DB è corretto ma il CSV/report no

---

## Output richiesto

Per ogni messaggio analizzato, restituisci una sezione nel formato seguente:

```text
raw_message_id:
telegram_message_id:
trader:
esito audit:
testo:
chain:
parse salvato:
parse attuale:
entity_check:
numeric_check:
entity_mismatches:
incongruenza:
causa probabile:
fix_area:
```

### Regole per i campi output

#### `chain`
Deve riportare in chiaro almeno:
- messaggio corrente
- parent diretto
- eventuale root signal
- eventuali link target
- eventuali comandi precedenti rilevanti

#### `parse salvato`
Riassumi almeno:
- `message_type`
- `intents`
- target principali
- entità principali
- eventuali warning rilevanti

#### `parse attuale`
Compilalo solo se è stato davvero verificato. Se non verificato, scrivi chiaramente:
- `non verificato`

#### `entity_check`
Riassumi se le entità sono:
- coerenti
- mancanti
- invertite
- parziali
- assegnate al campo sbagliato

#### `numeric_check`
Riporta esplicitamente eventuali problemi numerici:
- troncamento
- perdita di precisione
- perdita di ordini di grandezza
- separatori interpretati male
- decimali interpretati male

#### `fix_area`
Indica solo l'area probabile del problema, non proporre patch di codice.
Esempi:
- `classification`
- `intent mapping`
- `entity extraction`
- `numeric normalization`
- `target resolution`
- `trader resolution`
- `report export`
- `db refresh / reparse`

---

## Regole di comportamento

- non proporre fix di codice in questa skill
- non fermarti al parent diretto se il senso richiede reply-chain
- se il parser attuale differisce dal DB, segnalarlo esplicitamente
- se il caso dipende dal contesto chat, riportare la chain in chiaro
- non dichiarare bug solo perché il messaggio è corto o ambiguo
- non considerare corretto un parse solo perché `message_type` e intent sembrano plausibili: controlla anche entità e targeting
- se un numero nel testo non coincide con il parse, segnalarlo esplicitamente
- se un valore numerico perde ordini di grandezza o viene troncato, trattalo come bug serio
- se il DB non basta per ricostruire il contesto, dichiarare in modo trasparente il limite e usare `manual_review`

---

## Checklist minima per ogni caso

Prima di chiudere un caso, verifica di aver controllato tutto questo:

- testo raw letto integralmente
- parse salvato recuperato dal DB
- parent diretto verificato
- eventuale root signal verificato, se necessario
- eventuali link target verificati
- intent confrontati col significato umano
- targeting confrontato con la chain reale
- entità confrontate col testo
- numeri controllati per precisione e ordine
- eventuale parse attuale verificato, se utile
- categoria finale assegnata in modo esplicito

---

## Riepilogo finale obbligatorio

Chiudi sempre il report con un riepilogo aggregato contenente:
- numero casi analizzati
- numero bug veri
- numero record stale
- numero casi `not_a_bug`
- numero casi `manual_review`
- numero casi con problemi di entità
- numero casi con problemi di normalizzazione numerica

Se utile, aggiungi anche una distribuzione per:
- trader
- `message_type`
- categoria di bug

---

## Formula sintetica

Questa skill fa audit ad alta precisione dei risultati parser partendo da CSV/report e DB, ricostruisce la chain reale dei messaggi, confronta parse salvato e parse attuale quando serve, e verifica anche la fedeltà delle entità estratte, in particolare dei valori numerici.
