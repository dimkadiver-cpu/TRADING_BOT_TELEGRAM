# SESSION HANDOFF

## Stato corrente (aggiornato)
- Fase 0: completata
- Fase 1: completata
- Fase 2: completata per parser persistence
- Fase 3: validata
- Fase 4: implementata, ora in validazione estesa su dati reali

## Cosa e stato aggiunto di recente
1. Schema normalizzato unico `ParseResultNormalized`
2. Persistenza additiva in `parse_results.parse_result_normalized_json`
3. Normalizer da output parser corrente (regex/TA-core path)
4. Validator minimo non bloccante (warning only)
5. Replay parser aggiornato con stampa esempi normalizzati

## Compatibilita
- campi legacy in `parse_results` invariati
- nessuna rottura per consumer esistenti
- normalizzato disponibile come payload additivo

## Contratto normalizzato minimo
- `event_type`
- `trader_id`
- `source_chat_id`
- `source_message_id`
- `raw_text`
- `parser_mode`
- `confidence`
- `instrument`
- `side`
- `market_type`
- `entries`
- `stop_loss`
- `take_profits`
- `root_ref`
- `status`

## Event types canonici
- `NEW_SIGNAL`
- `UPDATE`
- `CANCEL_PENDING`
- `MOVE_STOP`
- `TAKE_PROFIT`
- `CLOSE_POSITION`
- `INFO_ONLY`
- `SETUP_INCOMPLETE`
- `INVALID`

## Prossimi step consigliati
1. replay su finestre piu ampie e analisi distribuzione event types
2. migliorare mapping subtype update multilingua
3. preparare fase 5 (matching update->trade) usando `root_ref` e linkage forte
