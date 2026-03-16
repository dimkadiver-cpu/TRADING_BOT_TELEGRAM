# Trader A parser logic (blueprint sintetico)

## 1) Flusso alto livello
1. `MinimalParserPipeline.parse` risolve modalità parser e trader profile.
2. Se esiste profile parser (`trader_a`), invoca `TraderAProfileParser.parse_message`.
3. Il risultato viene normalizzato in `ParseResultNormalized` con campi canonici.
4. Per `UPDATE` senza target concreto, pipeline aggiunge warning forte e `ACT_REQUEST_MANUAL_REVIEW`.

Riferimenti codice utili:
- `src/parser/pipeline.py`
- `src/parser/trader_profiles/trader_a/profile.py`
- `src/parser/normalization.py`

## 2) Trader A profile: separazione responsabilità
- `_classify_message`: decide `NEW_SIGNAL/UPDATE/...`
- `_extract_targets`: reply/link -> `target_refs`
- `_extract_intents`: mappa linguaggio in intenti (`U_MOVE_STOP`, `U_CLOSE_FULL`, ...)
- `_extract_entities`: estrae symbol/side/entry/sl/tp/close_scope...
- `_build_warnings`: warning trader-specific (es. update senza target)

## 3) Entry logic (signal market/limit)
- Entry levels: regex su `entry/вход` e pattern A/B.
- Order type PRIMARY:
  - MARKET se testo contiene marker "вход с текущих / at market"
  - LIMIT se marker limit
  - fallback prudente: LIMIT.
- Averaging (seconda entry) trattata come LIMIT opzionale.
- Entry plan canonico:
  - `SINGLE_MARKET`
  - `SINGLE_LIMIT`
  - `MARKET_WITH_LIMIT_AVERAGING`
  - `LIMIT_WITH_LIMIT_AVERAGING`

## 4) Linking e warning
- Priorità linking: reply, telegram link, altre referenze.
- `UPDATE` senza target genera warning multipli a più livelli:
  - trader profile
  - normalizzazione
  - pipeline policy globale.

## 5) Cosa riusare quando crei un nuovo trader parser
- Struttura metodi del profile parser Trader A.
- Pattern di test real-case (`tests/test_profile_real_cases.py`).
- Conversione entities -> canonical entries in `normalization.py`.

