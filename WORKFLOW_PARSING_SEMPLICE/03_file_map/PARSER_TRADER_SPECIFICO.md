# Parser Trader-Specifico - A Cosa Serve Ogni File

Questa pagina spiega i file del parser trader-specifico in modo operativo.

## Cartella base

Percorso: `src/parser/trader_profiles/`

### `base.py`
Serve a definire il contratto comune dei profili trader:

- `ParserContext`: dati in ingresso al parser trader-specifico (testo, reply, links, hashtags)
- `TraderParseResult`: output standard che il profilo deve restituire
- interfaccia/base parser: stesso metodo `parse_message(...)` per tutti i trader

In pratica: garantisce che profili diversi parlino la stessa lingua verso la pipeline comune.

### `registry.py`
Serve a risolvere quale parser usare per un trader.

In pratica:

- riceve un trader code (es. `trader_a`)
- restituisce l'istanza parser corretta

Evita `if/else` sparsi nella pipeline.

### `common_utils.py`
Helper condivisi tra profili trader-specifici.

Esempi:

- normalizzazione testo
- split righe
- estrazione link Telegram
- estrazione hashtag

In pratica: evita duplicazione di utility identiche tra trader.

## Profilo TA (legacy/attivo)

### `ta_profile.py`
Contiene la logica trader-specifica di TA.

In pratica:

- riconosce pattern TA
- estrae intent/update TA
- passa output nel formato comune usato dalla pipeline

### `traders/TA/parsing_rules.json`
Vocabolario/regole del trader TA (marker e pattern).

In pratica: permette tuning senza cambiare codice Python ogni volta.

## Profilo Trader A

Percorso: `src/parser/trader_profiles/trader_a/`

### `profile.py`
E il cuore del parser Trader A.

Responsabilità principali:

- preprocess del messaggio
- classificazione `message_type`
- estrazione `target_refs`
- estrazione `intents`
- estrazione `entities` minime
- estrazione `reported_results`
- warning/confidenza

In pratica: traduce testo grezzo Trader A in semantica comune del sistema.

### `parsing_rules.json`
Regole/marker testuali di Trader A.

In pratica:

- parole/frasi che aiutano classificazione
- marker intent (es. move stop, close, tp hit)
- marker admin/ignore

Serve a fare tuning rapido del comportamento senza refactor del parser.

### `debug_report.py`
Tool diagnostico per vedere come il parser Trader A classifica messaggi reali.

In pratica:

- legge messaggi (anche da DB test)
- mostra output parser leggibile (`message_type`, `intents`, `target_refs`, ecc.)

Serve a validare e migliorare le regole in modo guidato dai casi veri.

### `tests/`
Test del profilo Trader A.

File principali:

- `test_profile_smoke.py`: test base (non crash)
- `test_profile_phase1.py`: classificazione + target extraction
- `test_profile_phase2.py`: intent principali
- `test_profile_phase3.py`: entities/reported_results base
- `test_profile_real_cases.py`: regressioni su casi reali
- `test_profile_golden_cases.py`: golden suite stabile
- `test_debug_report_smoke.py`: smoke del tool debug

In pratica: protegge da regressioni quando tocchi `profile.py` o `parsing_rules.json`.

### `fixtures/README.md`
Spiega quali esempi reali raccogliere per futuri test.

## Come entra nel flusso comune

1. `pipeline.py` riceve il messaggio e il trader risolto.
2. usa `registry.py` per ottenere il parser trader-specifico.
3. chiama `parse_message(...)` del profilo.
4. converte il risultato nel normalized output comune.
5. applica mapping comune `intent -> action` (non nel profilo trader-specifico).

Regola importante:

Il profilo trader-specifico produce semantica (`message_type`, `intents`, `entities`),
mentre le `actions` vengono derivate nel layer comune.
