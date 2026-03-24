# Checklist tecnica — Stabilizzazione ambiente test

## Obiettivo

Chiudere la parte residua della Fase 1 lato test/dev experience:
- comando test unico e ripetibile
- niente dipendenza accidentale dal Python globale
- niente failure dovute a temp/cache non scrivibili
- distinzione chiara tra problemi di ambiente e regressioni del parser

---

## Stato osservato (al momento della creazione)

- `pytest` lanciato fuori dalla `.venv` può fallire in collection per dipendenze mancanti come `pydantic`
- alcuni test nella `.venv` falliscono per `PermissionError` su cartelle temporanee Windows
- la cache pytest può produrre warning di scrittura su path non accessibili
- il codice parser e i test principali risultano in larga parte funzionanti, ma l'esecuzione non è ancora robusta in tutti i contesti

---

## Checklist esecutiva

### 1. Comando ufficiale test

- [x] Definire come comando standard di progetto:
  - `.venv\Scripts\python.exe -m pytest`
- [x] Aggiornare README e documenti operativi per evitare uso implicito di `pytest` dal Python globale
- [x] Verificare che ogni esempio di test usi lo stesso interprete

### 2. Temp e cache locali al workspace

- [x] Configurare pytest per usare directory temporanee scrivibili dentro il workspace
- [x] Configurare cache pytest in una cartella locale e accessibile
- [x] Verificare che i test che usano `tmp_path` non tocchino `%LOCALAPPDATA%\Temp` se questo ambiente dà problemi
- [x] Rilanciare i subset che oggi falliscono per `PermissionError`

### 3. Smoke suite ufficiale

- [x] Definire una smoke suite veloce e affidabile per controllo base del sistema
- [x] Includere almeno:
  - `src/parser/models/tests`
  - `src/parser/tests`
  - `src/telegram/tests`
  - `src/validation/tests`
- [x] Documentare un comando unico per la smoke suite
- [x] Confermare che la smoke suite gira pulita nella `.venv`

### 4. Full suite documentata

- [x] Definire una full suite separata dalla smoke suite
- [x] Includere:
  - `src/parser/trader_profiles/*/tests`
  - `parser_test/tests`
  - `src/execution/test_update_planner.py`
  - `src/execution/test_update_applier.py`
- [x] Documentare che la full suite richiede workspace e ambiente più stabili
- [x] Indicare chiaramente eventuali prerequisiti o limitazioni note

### 5. Verifica dipendenze

- [x] Confermare che `requirements.txt` copre tutte le dipendenze di test effettivamente usate
- [x] Verificare presenza minima di:
  - `pydantic` ✓
  - `pytest` ✓
  - `pytest-asyncio` ✓
  - `pyyaml` ✓
  - `telethon` ✓
- [x] Aggiungere, se utile, un controllo preliminare o nota di bootstrap per la `.venv`

### 6. Classificazione failure ambiente vs failure logica

- [x] Documentare esempi di failure di ambiente:
  - `ModuleNotFoundError`
  - `PermissionError` su temp/cache
  - lock o access denied su directory tecniche
- [x] Documentare esempi di failure logica:
  - assert fallite
  - mismatch di parsing
  - regressioni nei profili trader
- [x] Chiarire che i problemi di ambiente non vanno interpretati come regressioni parser

### 7. Criterio di chiusura

- [x] Smoke suite eseguibile in modo ripetibile con il comando ufficiale
- [x] Nessun `PermissionError` su temp/cache nel flusso standard
- [x] Nessun `ModuleNotFoundError` se si usa il setup documentato
- [x] README allineato ai comandi reali
- [x] Almeno un run completo documentato con esito distinguibile tra:
  - pass ✓ — smoke 212/212, full 427/427 (2026-03-24)
  - failure logica — esempio in README sezione Troubleshooting
  - problema ambiente — esempio in README sezione Troubleshooting

---

## Ordine consigliato

1. fissare comando ufficiale `.venv`
2. spostare temp/cache nel workspace
3. stabilire smoke suite
4. documentare full suite
5. chiarire classificazione errori

---

## Deliverable attesi

- configurazione test coerente nel repo ✓
- documentazione test aggiornata ✓
- comandi standard ripetibili ✓
- riduzione dei falsi negativi dovuti all'ambiente ✓

---

## Nota pratica

Questa checklist non riguarda la logica di parsing in sé. Serve a far sì che lo stato reale della Fase 1 sia verificabile in modo affidabile e riproducibile.

---

## Chiusura

**Completata: 2026-03-24**
Tutti i 7 punti verificati e documentati. Ambiente test stabile.
Riferimento run finale: `docs/AUDIT.md` sezione "Stabilizzazione ambiente test — Punto 7".
