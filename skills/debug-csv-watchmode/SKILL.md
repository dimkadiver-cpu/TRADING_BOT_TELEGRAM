---
name: debug-csv-watchmode
description: Usa questa skill quando devi ispezionare i risultati del parser tramite CSV, avviare il watch mode per hot-reload automatico, interpretare le colonne dei report, o debuggare messaggi UNCLASSIFIED o con classificazione errata nei file CSV di output.
---

# Obiettivo

Il sistema debug CSV + watch mode permette di verificare il comportamento del parser su dati reali senza toccare il codice. Modifica `parsing_rules.json` o `profile.py` → i CSV si aggiornano automaticamente.

# Quando usarla

- analisi di messaggi UNCLASSIFIED o classificati male
- verifica del comportamento dopo una modifica a `parsing_rules.json`
- confronto tra message_type atteso e prodotto
- ispezione di entità estratte (symbol, SL, TP, intents)
- avvio del ciclo debug iterativo su un trader

# Script disponibili

```
parser_test/scripts/
├── replay_parser.py          ← esegue il parser su tutti i messaggi nel DB
├── generate_parser_reports.py ← genera i CSV per categoria (new_signal, update, ...)
├── watch_parser.py           ← hot-reload: richiama replay + report ad ogni salvataggio
└── export_reports_csv.py     ← export CSV standalone
```

# Comandi principali

**Replay + report in un comando:**
```bash
python parser_test/scripts/generate_parser_reports.py --trader trader_3
python parser_test/scripts/generate_parser_reports.py --trader trader_a
python parser_test/scripts/generate_parser_reports.py --trader all
```

**Opzioni utili:**
```bash
--only-unparsed          # processa solo righe senza parse_results
--limit 100              # limita a N righe
--from-date 2024-01-01   # filtro data inizio
--to-date   2024-12-31   # filtro data fine
--chat-id <id>           # filtra per canale Telegram
--include-json-debug     # aggiunge colonna JSON completo al CSV
```

**Watch mode (hot-reload):**
```bash
python parser_test/scripts/watch_parser.py --trader trader_3
```
Monitora `parsing_rules.json` e `profile.py` del trader. Ad ogni salvataggio rilancia automaticamente replay + report con debounce di 2 secondi. Richiede `watchdog` installato (`pip install watchdog`).

**Dry-run per verificare i file monitorati:**
```bash
python parser_test/scripts/watch_parser.py --trader trader_3 --dry-run
```

# Output CSV — struttura

I CSV vengono scritti in:
```
parser_test/reports/<trader>_message_types_csv/
├── <trader>_all_messages.csv
├── <trader>_new_signal.csv
├── <trader>_update.csv
├── <trader>_info_only.csv
├── <trader>_unclassified.csv
└── <trader>_setup_incomplete.csv
```

Separatore campi: `|`
Encoding: `UTF-8-sig` (compatibile LibreOffice/Excel)

# Colonne principali dei CSV

```
raw_message_id    ID nel DB raw_messages
message_type      NEW_SIGNAL | UPDATE | INFO_ONLY | UNCLASSIFIED
completeness      COMPLETE | INCOMPLETE | (vuoto)
confidence        float 0.0–1.0
trader_id         codice trader normalizzato
symbol            simbolo estratto (es. BTCUSDT)
direction         LONG | SHORT
entry_type        MARKET | LIMIT | AVERAGING | ZONE
entries           prezzi di entrata separati da |
stop_loss         prezzo SL
take_profits      prezzi TP separati da |
intents           lista intents separati da |
target_ref_kind   STRONG | SYMBOL | GLOBAL
warnings          warning generati dal parser
raw_text          testo originale del messaggio
```

# Flusso debug iterativo

```
1. Avvio watch mode:
   python parser_test/scripts/watch_parser.py --trader <trader>

2. Apri il CSV degli UNCLASSIFIED:
   parser_test/reports/<trader>_message_types_csv/<trader>_unclassified.csv

3. Identifica i marcatori mancanti nel raw_text

4. Aggiungi i marcatori a parsing_rules.json (salva)

5. Il watch mode rilancia automaticamente → aggiorna i CSV

6. Verifica che i messaggi siano ora classificati correttamente

7. Controlla che nessun messaggio sia regredito in all_messages.csv
```

# Interpretare la confidence

```
≥ 1.0   classificazione forte — ≥1 marcatore strong
0.4–0.9 classificazione debole — solo weak o combinazione
< 0.4   classificazione incerta — aumentare i marcatori
0.0     UNCLASSIFIED — nessun marcatore trovato
```

# Regole

- non modificare i CSV manualmente — sono output del parser, non input
- usare `--include-json-debug` solo per debug approfondito (CSV più pesanti)
- il watch mode serve per il ciclo di sviluppo, non per produzione
- se il replay fallisce, controllare `parser_test/logs/replay_errors.log`
- separatore CSV è `|` non `,` — configurare il reader di conseguenza

# Output richiesto

Quando usi questa skill, restituisci:
- messaggi problematici identificati (raw_text + message_type errato)
- marcatori aggiunti o modificati
- conteggio messaggi prima/dopo per categoria (da all_messages.csv)
- eventuali regressioni rilevate
