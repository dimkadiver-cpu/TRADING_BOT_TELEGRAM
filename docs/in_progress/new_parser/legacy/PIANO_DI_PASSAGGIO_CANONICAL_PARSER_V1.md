# PIANO_DI_PASSAGGIO_CANONICAL_PARSER_V1.md

## 1. Scopo

Questo documento descrive il **piano di passaggio completo** dal modello parser attuale al nuovo **Canonical Parser Model v1**.

L’obiettivo non è rifare subito tutto il parser live, ma portare il sistema a convergere in modo graduale, controllato e verificabile verso:

- un solo contratto canonico
- parser trader-specifici uniformi
- router e layer downstream allineati
- minore ambiguità, ridondanza e complessità nel Layer 4

---

## 2. Obiettivo finale

Portare il progetto da uno stato in cui esistono:

- output parser ibridi
- naming non uniforme
- differenze tra documentazione, router e payload reali
- confine non sempre chiaro tra signal, update, report e dettagli tecnici

a uno stato in cui esistono:

- un solo **Canonical Parser Model v1**
- parser trader-specifici che convergono nello stesso schema
- router che legge e persiste lo stesso contratto
- layer successivi che lavorano su un payload coerente e stabile

---

## 3. Principio guida della migrazione

La migrazione deve seguire questo ordine:

1. **bloccare il contratto**
2. **aggiungere compatibilità**
3. **misurare**
4. **migrare**
5. **ripulire**

Quindi:

- non si parte dalla riscrittura immediata di tutti i parser
- non si parte dal refactor del router
- non si parte dalla pulizia DB

Si parte dal nuovo contratto canonico come **nuovo punto di convergenza**.

---

## 4. Stato di partenza

Ad oggi sono già stati fatti questi lavori:

- analisi del Layer 4 attuale
- valutazione di ambiguità, ridondanza e complessità
- definizione del nuovo **Canonical Parser Model v1**
- separazione chiara tra:
  - `SIGNAL`
  - `UPDATE`
  - `REPORT`
  - `INFO`
- riduzione degli update canonici a 5:
  - `SET_STOP`
  - `CLOSE`
  - `CANCEL_PENDING`
  - `MODIFY_ENTRIES`
  - `MODIFY_TARGETS`
- definizione del nuovo targeting unificato
- definizione del nuovo entry model:
  - `entry_structure` globale
  - `entry_type` solo per leg
- definizione delle regole per messaggi compositi
- redazione della specifica `.md`
- creazione dello schema Pydantic `.py`
- verifica finale di coerenza tra documentazione e schema

Quindi il lavoro di **progettazione del contratto v1** è considerato sostanzialmente chiuso.

---

## 5. Strategia generale di passaggio

La strategia consigliata è una **migrazione a doppio binario temporaneo**:

- il parser attuale continua a funzionare
- il nuovo modello v1 viene introdotto come nuovo target canonico
- si aggiunge un adapter/normalizer che converte il vecchio output nel nuovo schema
- si misurano differenze e casi problematici
- solo dopo si migrano i parser trader-specifici uno alla volta
- infine si rimuovono i layer legacy

Questa strategia riduce il rischio operativo.

---

# 6. FASI DEL PIANO

## FASE 1 — Congelamento del contratto v1

### Scopo
Bloccare formalmente il nuovo modello come base ufficiale di lavoro.

### Attività
- scegliere la revisione finale ufficiale della specifica markdown
- scegliere la revisione finale ufficiale dello schema Pydantic
- spostare i file ufficiali in una posizione stabile del repo
- eliminare o marcare chiaramente come obsolete le revisioni intermedie
- aggiungere nota di stato: **Canonical Parser Model v1 frozen**

### Deliverable
- file markdown ufficiale
- file Pydantic ufficiale
- convenzione interna su come evolvere il modello senza fork paralleli

### Exit criteria
- esiste una sola spec ufficiale
- esiste un solo schema ufficiale
- nessuna ambiguità su quale revisione usare

---

## FASE 2 — Fixture canoniche di riferimento

### Scopo
Creare un set di esempi minimi ma completi per testare il modello v1.

### Attività
Creare una cartella dedicata, ad esempio:

- `tests/parser_v1_fixtures/`

e inserire esempi JSON/YAML per almeno questi casi:

### SIGNAL
- segnale completo `ONE_SHOT`
- segnale `TWO_STEP`
- segnale `RANGE`
- segnale `LADDER`
- segnale `PARTIAL`

### UPDATE
- `SET_STOP` a prezzo
- `SET_STOP` a breakeven
- `SET_STOP` a TP level
- `CLOSE` partial
- `CLOSE` full
- `CANCEL_PENDING`
- `MODIFY_ENTRIES`
- `MODIFY_TARGETS`

### REPORT
- `ENTRY_FILLED`
- `TP_HIT`
- `STOP_HIT`
- `BREAKEVEN_EXIT`
- `FINAL_RESULT`

### compositi
- `UPDATE + REPORT`

### INFO
- messaggio informativo puro

### Deliverable
- dataset di esempi canonici v1
- naming consistente
- casi edge principali già coperti

### Exit criteria
- esiste una suite minima di esempi leggibile e condivisa
- il modello v1 è testabile anche senza parser reali

---

## FASE 3 — Validazione automatica del modello v1

### Scopo
Verificare in modo automatico che i fixture canonici rispettino il modello.

### Attività
- creare test automatici sul file Pydantic v1
- validare tutti i fixture
- aggiungere casi negativi:
  - `SIGNAL + UPDATE`
  - `ONE_SHOT` con 0 leg
  - `TWO_STEP` con 1 leg
  - `INFO` con payload business valorizzati
  - `REPORT` senza `event` né `reported_result`

### Deliverable
- test unitari schema v1
- casi positivi e negativi

### Exit criteria
- tutti i fixture validi passano
- i fixture volutamente invalidi falliscono correttamente

---

## FASE 4 — Adapter / Normalizer dal parser attuale al nuovo v1

### Scopo
Inserire un layer di conversione tra il parser attuale e il nuovo modello canonico.

### Principio
Non si riscrivono ancora i parser trader-specifici.  
Si prende l’output attuale e lo si converte nel nuovo v1.

### Attività
Creare un modulo dedicato, ad esempio:

- `src/parser_v1/normalizer.py`

che riceve in input:
- `TraderParseResult` attuale
- eventuali metadati del router
- eventuali info di validazione

e produce:
- `CanonicalMessage` v1

### Compiti del normalizer
- mappare `message_type` attuale verso `primary_class`
- mappare intent legacy verso i nuovi `intents`
- costruire:
  - `targeting`
  - `signal`
  - `update`
  - `report`
- convertire:
  - vecchi nomi entry
  - vecchi nomi stop
  - vecchi report
- distinguere bene update operativi da report
- classificare i casi `PARTIAL`

### Deliverable
- modulo normalizer
- mapping documentato da vecchio schema a v1

### Exit criteria
- il normalizer produce `CanonicalMessage` valido
- i casi principali dei trader attuali sono convertibili

---

## FASE 5 — Modalità shadow / doppia emissione

### Scopo
Introdurre il nuovo modello senza cambiare ancora il comportamento operativo del sistema.

### Strategia
Il parser/ router continua a usare il flusso attuale, ma in parallelo produce anche il nuovo payload v1.

### Attività
Aggiungere una modalità tipo:

- `parser_v1_shadow_mode = true`

in cui:

- il sistema continua a generare l’output parser attuale
- in parallelo genera anche `CanonicalMessage` v1
- salva entrambi per confronto

### Possibili modalità di persistenza
- colonna JSON aggiuntiva in `parse_results`
- tabella nuova dedicata
- file di audit separati
- log strutturati

### Deliverable
- doppia emissione attivabile
- dati confrontabili tra vecchio e nuovo modello

### Exit criteria
- il sistema produce il nuovo payload senza rompere il flusso esistente
- il nuovo payload è osservabile e confrontabile

---

## FASE 6 — Audit differenziale

### Scopo
Misurare quanto il nuovo modello v1 corrisponde davvero all’output attuale e dove emergono gap.

### Attività
Creare report di confronto su dataset reali:

- vecchio output vs nuovo `CanonicalMessage`
- coverage per trader
- coverage per classe messaggio
- percentuale di:
  - `PARSED`
  - `PARTIAL`
  - `UNCLASSIFIED`
  - `ERROR`

### Verifiche da fare
- correttezza `primary_class`
- correttezza `targeting`
- correttezza `entry_structure`
- correttezza mapping update
- correttezza eventi report
- casi compositi

### Deliverable
- report differenziale
- lista dei mismatch
- backlog di correzioni del normalizer

### Exit criteria
- i mismatch principali sono noti e classificati
- il modello v1 regge sui dati reali

---

## FASE 7 — Stabilizzazione del normalizer

### Scopo
Correggere il normalizer fino a portarlo a un livello sufficiente di affidabilità.

### Attività
- correggere mapping errati
- gestire edge cases reali
- completare le trasformazioni per tutti i profili principali
- ridurre i falsi `PARTIAL`
- ridurre i falsi `UNCLASSIFIED`

### Deliverable
- normalizer stabile
- suite di test aggiornata
- fixture reali aggiuntivi

### Exit criteria
- il nuovo modello v1 è affidabile come rappresentazione canonica del parser attuale

---

## FASE 8 — Integrazione del router con il modello v1

### Scopo
Portare il router a leggere e trattare il nuovo modello canonico come formato primario.

### Attività
- aggiornare il router per ricevere `CanonicalMessage`
- decidere cosa persistere:
  - colonne sintetiche
  - JSON canonico completo
- rendere il modello v1 il nuovo payload centrale del Layer 4

### Principio
Le colonne DB sintetiche devono restare **supporto per query rapide**, ma la vera fonte di verità deve diventare:

- il JSON canonico v1

### Deliverable
- router compatibile con `CanonicalMessage`
- strategia di persistenza chiara

### Exit criteria
- il router può lavorare nativamente col nuovo schema
- il vecchio output non è più necessario per il flusso principale

---

## FASE 9 — Migrazione progressiva dei parser trader-specifici

### Scopo
Far sì che i parser trader-specifici emettano direttamente il nuovo modello, senza passare per il vecchio schema + normalizer.

### Strategia
Migrazione uno per volta, non big-bang.

### Ordine consigliato
Partire dai parser:
- più semplici
- più stabili
- meno ambigui

Lasciare per ultimi quelli:
- più ricchi di eccezioni
- più ambigui
- più lontani dal nuovo modello

### Attività per ogni parser
- adattare l’estrazione interna
- far produrre direttamente il nuovo schema
- confrontare con il normalizer
- passare in modalità nativa

### Deliverable
- parser trader-specifici v1-native

### Exit criteria
- almeno i trader principali emettono direttamente `CanonicalMessage`

---

## FASE 10 — Deprecazione del modello legacy

### Scopo
Rimuovere gradualmente il vecchio contratto parser.

### Attività
- deprecare campi vecchi non più necessari
- rimuovere dipendenze downstream dal vecchio schema
- rimuovere adapter temporanei dove non più utili
- ripulire documentazione e test legacy

### Deliverable
- contratto parser unico
- rimozione dei doppi binari non più necessari

### Exit criteria
- il sistema usa un solo modello parser
- il vecchio schema non è più path attivo

---

# 7. Piano dati / persistenza

## Principio
Durante la migrazione ci sono due possibilità:

### Opzione A — doppia persistenza temporanea
Salvare:
- output legacy
- output v1

### Opzione B — JSON v1 + campi legacy derivati
Più avanti il router può salvare:
- JSON canonico v1 come verità
- poche colonne sintetiche per query/report

## Raccomandazione
Usare:
- **doppia persistenza temporanea** nelle fasi shadow
- poi convergere a:
  - JSON canonico v1 come source of truth
  - colonne sintetiche solo come supporto

---

# 8. Rischi principali

## Rischio 1 — Migrazione troppo precoce del router
Se il router viene adattato prima che il normalizer sia stabile, si rischiano regressioni.

### Mitigazione
- introdurre prima la shadow mode
- fare audit differenziale

## Rischio 2 — Reintrodurre complessità nel v1
Durante la migrazione si potrebbe essere tentati di aggiungere eccezioni, campi e casi speciali.

### Mitigazione
- proteggere il contratto v1
- trattare ogni eccezione come mapping o normalizzazione, non come estensione immediata del contratto

## Rischio 3 — Conflitto tra intent legacy e payload canonici
Il vecchio sistema potrebbe ancora appoggiarsi troppo agli intent.

### Mitigazione
- ricordare sempre la regola:
  - `intents` = supporto semantico
  - payload canonici = verità business

## Rischio 4 — Migrazione simultanea di tutti i parser
Porterebbe alta instabilità.

### Mitigazione
- migrare un trader alla volta

---

# 9. Criteri di successo

Il piano è riuscito quando:

1. esiste una sola specifica ufficiale del parser
2. esiste uno schema Pydantic ufficiale coerente
3. esiste un adapter dal vecchio output al nuovo modello
4. il router può leggere il nuovo modello
5. almeno i trader principali emettono nativamente il v1
6. il vecchio schema non è più necessario nel path principale
7. il Layer 4 non è più ibrido

---

# 10. Roadmap sintetica

## Step 1
Congelare contratto v1 ufficiale

## Step 2
Creare fixture canoniche + test schema

## Step 3
Costruire normalizer dal vecchio schema al v1

## Step 4
Attivare shadow mode con doppia emissione

## Step 5
Fare audit differenziale su dataset reali

## Step 6
Stabilizzare normalizer

## Step 7
Adattare router al nuovo schema

## Step 8
Migrare parser trader-specifici uno per volta

## Step 9
Deprecare il modello legacy

---

# 11. Sintesi finale

Il passaggio non va trattato come un refactor singolo del parser.

Va trattato come una **migrazione architetturale controllata**, in cui:

- il nuovo Canonical Parser Model v1 diventa il centro
- il vecchio sistema viene assorbito gradualmente
- il router e i parser trader-specifici convergono progressivamente
- la complessità del Layer 4 viene ridotta senza interrompere subito l’operatività
