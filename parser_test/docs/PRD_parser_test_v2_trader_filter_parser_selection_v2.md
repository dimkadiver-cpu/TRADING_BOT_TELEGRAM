# PRD — Parser Test v2: import DB, risoluzione trader, selezione messaggi e selezione parser

## 1. Scopo

Definire un flusso robusto per `parser_test` v2 che permetta di:

1. scaricare/importare messaggi Telegram in un DB di test;
2. impostare un trader di default quando la sorgente è mono-trader;
3. gestire DB multitrader usando la stessa logica di risoluzione trader del live;
4. selezionare quali messaggi parsare in base al trader effettivo;
5. selezionare quale parser/profilo usare per il parsing;
6. produrre CSV puliti, auditabili e non contaminati da trader fuori filtro.

Il punto centrale è separare quattro concetti che oggi rischiano di essere mescolati:

```text
source_trader_id        = trader noto dalla sorgente/import, se disponibile
resolved_trader_id      = trader effettivo risolto con la logica live/fallback
message_trader_filter   = quali messaggi voglio includere nel replay
parser_profile          = quale parser/profilo voglio usare per parsare
```

---

## 2. Problema attuale

Nel replay v2 attuale, `--trader trader_a` viene interpretato di fatto come fallback quando il trader non è risolto, non come filtro reale dei messaggi.

Questo produce un difetto pratico:

```text
DB multitrader + --trader trader_a = CSV con possibili messaggi di altri trader
```

Inoltre manca una separazione esplicita tra:

1. selezionare i messaggi di Trader A;
2. usare il parser/profilo Trader A;
3. assumere Trader A sui messaggi non risolti;
4. scaricare/importare da una sorgente già nota come Trader A.

Queste sono operazioni diverse e devono avere opzioni diverse.

---

## 3. Requisiti utente

### 3.1 Scaricare DB + impostare già un trader di default

Scenario: si scaricano messaggi da una sorgente che si sa essere mono-trader, per esempio un canale o topic dedicato a Trader A.

Requisito:

```text
Durante import/download, poter valorizzare source_trader_id = trader_a.
```

Questo serve a creare un DB già pulito dal punto di vista della sorgente.

### 3.2 Scaricare DB con trader multipli

Scenario: si scaricano messaggi da un canale/topic dove possono comparire più trader.

Requisito:

```text
Non impostare forzatamente un source_trader_id unico.
Usare la logica live per risolvere il trader effettivo.
```

La risoluzione deve usare lo stesso criterio del live, quindi mapping chat/topic, reply/context, source metadata e regole del resolver.

### 3.3 Parsare DB selezionando il parser

Scenario: si vuole scegliere con quale parser/profilo parsare i messaggi.

Requisito:

```text
Il parser/profilo da usare deve essere selezionabile in modo esplicito.
```

Esempi:

```bash
--parser-system parser_v2 --parser-profile trader_a
--parser-system parser_v2 --parser-profile auto
--parser-system parser_v2 --parser-profile trader_a_experimental
```

### 3.4 Parsare DB selezionando i messaggi del trader

Scenario: da un DB multitrader voglio parsare solo i messaggi di Trader A.

Requisito:

```text
Il filtro dei messaggi deve essere separato dal parser usato.
```

Esempio:

```bash
--message-trader-filter trader_a --parser-profile trader_a
```

---

## 4. Terminologia corretta

| Concetto | Nome consigliato | Significato |
|---|---|---|
| Trader noto dalla sorgente | `source_trader_id` | Valore salvato in `raw_messages`, se noto in import/download |
| Trader risolto operativamente | `resolved_trader_id` / `effective_trader_id` | Valore deciso dal resolver, usando la logica live |
| Filtro messaggi | `--message-trader-filter` o `--trader-filter` | Include solo messaggi risolti come quel trader |
| Trader assunto se non risolto | `--assume-trader` | Fallback solo per messaggi senza trader risolto |
| Parser da usare | `--parser-profile` | Profilo/parser applicato al messaggio selezionato |
| Sistema parser | `--parser-system` | `parser_v2`, futuro `legacy`, futuro `experimental`, ecc. |

Nota: per semplicità si può mantenere `--trader-filter` invece di `--message-trader-filter`, ma il significato deve essere documentato come filtro dei messaggi, non parser.

---

## 5. Flusso target completo

```text
DOWNLOAD / IMPORT
  ↓
raw_messages
  - source_chat_id
  - source_topic_id
  - source_trader_id, se noto dalla sorgente
  - raw_text
  - reply_to_message_id
  ↓
TRADER RESOLUTION
  - usa stessa logica del live
  - produce resolved_trader_id
  - non sovrascrive source_trader_id se già valido
  ↓
MESSAGE FILTER
  - applica --trader-filter / --message-trader-filter
  ↓
PARSER SELECTION
  - auto: parser_profile = resolved_trader_id
  - fixed: parser_profile = valore esplicito
  ↓
PARSING
  - esegue parser scelto
  ↓
RESULTS
  - parser_results_v2
  - CSV risultati effettivi
  - CSV audit opzionale
```

---

## 6. Import/download DB

### 6.1 Import mono-trader

Nuova opzione consigliata:

```bash
--default-source-trader trader_a
```

Significato:

```text
Durante l'import, se il messaggio non ha già un source_trader_id, valorizza source_trader_id=trader_a.
```

Usare solo quando la sorgente è realmente mono-trader.

Esempio:

```bash
python parser_test/scripts/import_history.py ^
  --db-path C:\TeleSignalBot\parser_test\db\trader_a.sqlite3 ^
  --chat-id -3722628653 ^
  --topic-id 3 ^
  --default-source-trader trader_a
```

### 6.2 Import multitrader

Per DB multitrader non si deve usare `--default-source-trader` globale.

Comportamento corretto:

```text
source_trader_id resta NULL se non è noto dalla sorgente.
Il trader effettivo viene risolto dopo, usando il resolver live.
```

Opzione consigliata:

```bash
--resolve-traders live
```

oppure fase separata:

```bash
python parser_test/scripts/resolve_traders.py ^
  --db-path C:\TeleSignalBot\parser_test\db\multi.sqlite3 ^
  --resolver-mode live
```

La fase separata è più auditabile, perché consente di verificare prima la qualità della risoluzione trader.

---

## 7. Regole di risoluzione trader

La priorità corretta è:

```text
1. source_trader_id, se presente
2. resolver live / EffectiveTraderResolver
3. --assume-trader, se fornito
4. unresolved
```

`source_trader_id` non deve essere sovrascritto dal resolver.

Esempio:

| source_trader_id | resolver | assume_trader | resolved_trader_id |
|---|---|---|---|
| trader_a | trader_b | trader_a | trader_a |
| trader_b | trader_a | trader_a | trader_b |
| NULL | trader_a | NULL | trader_a |
| NULL | NULL | trader_a | trader_a |
| NULL | NULL | NULL | unresolved |

---

## 8. Selezione messaggi da parsare

Nuova opzione:

```bash
--trader-filter trader_a
```

oppure nome più esplicito:

```bash
--message-trader-filter trader_a
```

Significato:

```text
Dopo la risoluzione trader, processa solo i messaggi con resolved_trader_id = trader_a.
```

Non deve essere usato per scegliere il parser. Serve solo a scegliere i messaggi.

### 8.1 Esempio: DB multitrader, solo messaggi Trader A

```bash
python parser_test/scripts/replay_parser_v2.py ^
  --db-path C:\TeleSignalBot\parser_test\db\multi.sqlite3 ^
  --trader-filter trader_a ^
  --parser-system parser_v2 ^
  --parser-profile trader_a
```

### 8.2 Esempio: DB mono-trader con fallback

```bash
python parser_test/scripts/replay_parser_v2.py ^
  --db-path C:\TeleSignalBot\parser_test\db\trader_a.sqlite3 ^
  --trader-filter trader_a ^
  --assume-trader trader_a ^
  --parser-system parser_v2 ^
  --parser-profile trader_a
```

Usare `--assume-trader` solo se la sorgente è affidabile come mono-trader.

---

## 9. Selezione parser/profilo

Nuova opzione obbligatoria o esplicita:

```bash
--parser-profile trader_a
```

Valori possibili:

```text
auto
trader_a
trader_b
trader_c
trader_a_experimental
```

Il set reale dipende dal registry dei parser disponibili.

### 9.1 Modalità `auto`

```bash
--parser-profile auto
```

Significato:

```text
Usa come parser_profile il resolved_trader_id del messaggio.
```

Esempio:

```text
resolved_trader_id = trader_a → parser_profile = trader_a
resolved_trader_id = trader_b → parser_profile = trader_b, se disponibile
```

Se il profilo non esiste:

```text
SKIPPED_UNSUPPORTED_PARSER_PROFILE
```

### 9.2 Modalità fixed

```bash
--parser-profile trader_a
```

Significato:

```text
Usa sempre il parser/profilo trader_a sui messaggi selezionati.
```

Questa modalità è utile per testare un parser specifico su un sottoinsieme controllato di messaggi.

### 9.3 Regola di sicurezza

Se `--parser-profile trader_a` e il messaggio selezionato ha `resolved_trader_id != trader_a`, il replay deve permetterlo solo se il filtro è esplicito o se è abilitata una modalità sperimentale.

Regola consigliata:

```text
Di default: parser_profile fixed può parsare solo messaggi che passano il trader_filter coerente.
Override esplicito: --allow-cross-profile-parse
```

Questo evita di parsare accidentalmente messaggi Trader B con parser Trader A.

---

## 10. Compatibilità con `--trader`

Il vecchio argomento:

```bash
--trader trader_a
```

è ambiguo e va deprecato.

Compatibilità temporanea raccomandata:

```text
--trader trader_a = alias di --trader-filter trader_a
```

Con warning:

```text
[warning] --trader is deprecated; use --trader-filter for message selection or --assume-trader for fallback.
```

Non deve più essere usato come fallback silenzioso.

---

## 11. Stati da tracciare nei risultati

Nei risultati del run devono essere distinguibili:

```text
OK
UNRESOLVED_TRADER
SKIPPED_TRADER_FILTER
SKIPPED_UNSUPPORTED_TRADER
SKIPPED_UNSUPPORTED_PARSER_PROFILE
PARSER_ERROR
```

Consigliato salvare anche gli skip in `parser_results_v2`, così il run è auditabile.

---

## 12. CSV

### 12.1 CSV risultati effettivi

Deve esportare solo record parser validi:

```sql
WHERE run_id = ?
  AND error_status = 'OK'
```

Se è richiesto Trader A only:

```sql
AND trader_id = 'trader_a'
```

### 12.2 CSV audit

Deve includere anche skip e motivi:

```text
raw_message_id
source_trader_id
resolved_trader_id
parser_profile
error_status
error_message
source_chat_id
source_topic_id
telegram_message_id
text_preview
```

Questo CSV serve per capire perché certi messaggi sono stati esclusi.

---

## 13. Esempi operativi

### 13.1 Scarico mono-trader + replay Trader A

```bash
python parser_test/scripts/import_history.py ^
  --db-path C:\TeleSignalBot\parser_test\db\trader_a.sqlite3 ^
  --chat-id -3722628653 ^
  --topic-id 3 ^
  --default-source-trader trader_a

python parser_test/scripts/replay_parser_v2.py ^
  --db-path C:\TeleSignalBot\parser_test\db\trader_a.sqlite3 ^
  --trader-filter trader_a ^
  --parser-system parser_v2 ^
  --parser-profile trader_a ^
  --force-reparse
```

### 13.2 Scarico multitrader + risoluzione live + replay solo Trader A

```bash
python parser_test/scripts/import_history.py ^
  --db-path C:\TeleSignalBot\parser_test\db\multi.sqlite3 ^
  --chat-id -3722628653

python parser_test/scripts/resolve_traders.py ^
  --db-path C:\TeleSignalBot\parser_test\db\multi.sqlite3 ^
  --resolver-mode live

python parser_test/scripts/replay_parser_v2.py ^
  --db-path C:\TeleSignalBot\parser_test\db\multi.sqlite3 ^
  --trader-filter trader_a ^
  --parser-system parser_v2 ^
  --parser-profile trader_a ^
  --force-reparse
```

### 13.3 Replay multitrader con parser automatico

```bash
python parser_test/scripts/replay_parser_v2.py ^
  --db-path C:\TeleSignalBot\parser_test\db\multi.sqlite3 ^
  --parser-system parser_v2 ^
  --parser-profile auto ^
  --force-reparse
```

In questo caso ogni messaggio usa il parser associato al proprio `resolved_trader_id`, se disponibile.

### 13.4 Test sperimentale: messaggi Trader A con parser alternativo

```bash
python parser_test/scripts/replay_parser_v2.py ^
  --db-path C:\TeleSignalBot\parser_test\db\multi.sqlite3 ^
  --trader-filter trader_a ^
  --parser-system parser_v2 ^
  --parser-profile trader_a_experimental ^
  --force-reparse
```

---

## 14. Modifiche richieste al codice

### 14.1 Import/download

File probabili:

```text
parser_test/scripts/import_history.py
parser_test/db/schema.py
```

Aggiungere:

```bash
--default-source-trader
```

Comportamento:

```text
Se fornito, valorizza raw_messages.source_trader_id quando non già disponibile.
```

### 14.2 Risoluzione trader

Creare o integrare:

```text
parser_test/scripts/resolve_traders.py
```

Oppure integrare nel replay:

```bash
--resolver-mode live
```

Raccomandazione: fase separata `resolve_traders.py`, perché è più chiara e auditabile.

### 14.3 Replay parser

File:

```text
parser_test/scripts/replay_parser_v2.py
```

Aggiungere:

```bash
--trader-filter
--assume-trader
--parser-system
--parser-profile
--allow-cross-profile-parse
```

Deprecare:

```bash
--trader
```

### 14.4 Results storage

File probabili:

```text
src/storage/parser_runs.py
parser_test/db/schema.py
```

Valutare aggiunta o uso di campi esistenti:

```text
resolved_trader_id / trader_id
parser_profile
error_status
error_message
```

Se non si vuole cambiare schema, usare `parser_results_v2.trader_id` come trader risolto effettivo.

---

## 15. Test di accettazione

### 15.1 Import mono-trader

Dato:

```bash
--default-source-trader trader_a
```

Atteso:

```text
raw_messages.source_trader_id = trader_a per i messaggi importati.
```

### 15.2 Import multitrader

Dato:

```text
nessun default source trader
```

Atteso:

```text
source_trader_id non viene forzato globalmente.
```

### 15.3 Risoluzione live

Dato DB multitrader.

Atteso:

```text
Il resolver assegna resolved_trader_id coerente con la logica live.
source_trader_id, se presente, ha priorità.
```

### 15.4 Replay Trader A only

Comando:

```bash
--trader-filter trader_a --parser-profile trader_a
```

Atteso:

```text
CSV effettivo contiene solo error_status=OK e trader_id=trader_a.
```

### 15.5 Parser selection fixed

Comando:

```bash
--trader-filter trader_a --parser-profile trader_a_experimental
```

Atteso:

```text
Sono selezionati solo messaggi Trader A, ma parsati con parser trader_a_experimental.
```

### 15.6 Parser selection auto

Comando:

```bash
--parser-profile auto
```

Atteso:

```text
Ogni messaggio usa il parser corrispondente al proprio resolved_trader_id.
I trader senza parser disponibile vengono marcati SKIPPED_UNSUPPORTED_PARSER_PROFILE.
```

---

## 16. Decisione finale

Il PRD precedente è compatibile solo in parte.

Copre bene:

```text
selezionare i messaggi del trader da parsare
separare filtro da fallback
CSV non contaminato
```

Non copre abbastanza:

```text
scaricare/importare DB con trader di default
scaricare/importare DB multitrader con risoluzione come live
selezionare esplicitamente il parser/profilo con cui parsare
separare parser_profile da trader_filter
```

La versione corretta deve quindi usare questo modello:

```text
IMPORT:
  --default-source-trader        # solo per sorgenti mono-trader

RESOLUTION:
  resolver live / resolve_traders.py

REPLAY:
  --trader-filter               # quali messaggi parsare
  --assume-trader               # fallback per non risolti
  --parser-system               # quale sistema parser
  --parser-profile              # quale parser/profilo usare
```

Regola fondamentale:

```text
Il trader del messaggio e il parser usato per parsarlo non sono la stessa cosa.
```
