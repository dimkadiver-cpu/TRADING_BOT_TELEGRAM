---
name: generate-trader-parser-from-dataset
description: Usa questa skill quando devi costruire o aggiornare un parser trader-specific partendo da esempi etichettati (testo messaggio + commenti). Produce spec parser, pattern, mapping canonico e test minimi pronti per integrazione.
---

# Obiettivo
Generare in modo ripetibile un parser trader-specifico partendo da un dataset esempi (`text`, `comment`) senza rompere lo schema canonico eventi.

## Quando usarla
- hai un nuovo trader e un dataset esempi
- devi migliorare un parser esistente con nuovi pattern reali
- devi convertire feedback manuali (`comment`) in regole deterministiche + test

## Input atteso
Accetta uno dei seguenti formati:
- CSV con colonne minime: `text`, `comment`
- JSONL con chiavi minime: `text`, `comment`

Colonne/chiavi opzionali (fortemente consigliate):
- `expected_message_type`
- `expected_event_type`
- `expected_symbol`
- `expected_side`
- `expected_root_ref`
- `note`

## Workflow operativo
1. **Carica dataset e profila qualità**
   - Valida righe vuote / duplicati / encoding.
   - Estrai lingua, emoji, hashtag, link Telegram, reply hint.
2. **Deriva pattern candidati**
   - Se `comment` contiene indicazioni tipo *close full*, *move stop*, *tp hit*, trasformale in candidati `intent_markers`.
   - Se il testo contiene setup completo (entry+sl+tp), candida `NEW_SIGNAL`.
3. **Separa livelli logici**
   - classificazione (`NEW_SIGNAL`, `UPDATE`, ...)
   - estrazione entità (symbol, side, entries, sl, tps)
   - linking (`reply`, link Telegram, heuristic)
   - normalizzazione canonica.
4. **Convergi allo schema canonico**
   - campi minimi: `event_type`, `trader_id`, `source_chat_id`, `source_message_id`, `raw_text`, `parser_mode`, `confidence`, `instrument`, `side`, `market_type`, `entries`, `stop_loss`, `take_profits`, `root_ref`, `status`.
5. **Genera test minimi**
   - golden case per tipo messaggio
   - casi ambigui
   - casi di linking debole (warning attesi)

## Script consigliato
Usa `scripts/dataset_to_parser_spec.py` per produrre una bozza tecnica:
- summary dataset
- marker candidati da `comment`
- skeleton `classification_markers`
- skeleton `intent_markers`
- esempi test seed.

Esempi:
```bash
python skills/generate-trader-parser-from-dataset/scripts/dataset_to_parser_spec.py \
  --input data/trader_x_examples.csv \
  --trader-id trader_x \
  --out /tmp/trader_x_spec.json
```

```bash
python skills/generate-trader-parser-from-dataset/scripts/dataset_to_parser_spec.py \
  --input data/trader_x_examples.jsonl \
  --trader-id trader_x
```

## Integrazione nel codice
Per implementare la bozza nel progetto attuale, segui `references/trader-a-parser-logic.md` come blueprint architetturale:
- parser profile trader-specifico
- mapping intent/action
- normalizzazione evento canonico
- warning policy su update senza target.

## Output che devi restituire all'utente
1. Cosa hai cambiato
2. File toccati
3. Rischi residui
4. Test eseguiti/mancanti
5. Follow-up consigliati

## Guardrail
- Non inventare livelli prezzo mancanti.
- Se linking è debole: abbassa confidenza e marca warning.
- Non mescolare parsing trader-specifico con normalizzazione globale.
- Mantieni categorie evento canoniche del progetto.
