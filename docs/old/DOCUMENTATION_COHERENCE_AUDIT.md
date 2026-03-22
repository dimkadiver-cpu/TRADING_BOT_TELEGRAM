# DOCUMENTATION COHERENCE AUDIT

## Obiettivo

Verificare la coerenza tra documentazione e stato reale del codice nel repository.

## Data

Audit aggiornato sul branch corrente.

---

## Esito sintetico

Stato complessivo: **parzialmente coerente**.

- **Area parser + ingestion + persistenza parse**: buona coerenza.
- **Area execution/risk/state machine/bot commands**: documentazione piu avanzata dell'implementazione reale.
- **Area DB schema**: il documento include sia tabelle gia migrate sia tabelle target future.
- **Area parser_test**: setup documentato coerente, ma restano gap nei test TA attuali.

---

## Coerenza per area

### 1) Ingestion Telegram e parser minimo
**Valutazione: coerente.**

La documentazione descrive una pipeline che esiste davvero nel codice:
- listener Telegram con filtro chat e ingestione raw
- risoluzione trader effettivo
- eligibility + linking forte
- pipeline parser minima con output normalizzato
- persistenza in `parse_results`

### 2) Contratto canonico eventi e normalizzazione
**Valutazione: coerente.**

I tipi evento canonici documentati sono allineati alla normalizzazione implementata:
- `NEW_SIGNAL`
- `UPDATE`
- `CANCEL_PENDING`
- `MOVE_STOP`
- `TAKE_PROFIT`
- `CLOSE_POSITION`
- `INFO_ONLY`
- `SETUP_INCOMPLETE`
- `INVALID`

Sono presenti anche validazioni non bloccanti e warning, in linea con i documenti tecnici.

### 3) Execution, risk engine, planner, state machine, bot commands
**Valutazione: documentazione in anticipo sul codice.**

I documenti di dominio sono utili, ma i moduli runtime corrispondenti sono ancora placeholder o TODO:
- `src/execution/risk_gate.py`
- `src/execution/planner.py`
- `src/execution/state_machine.py`
- `src/telegram/bot.py`
- `src/exchange/adapter.py`

Questi documenti vanno quindi letti come **target design** e non come comportamento gia disponibile end-to-end.

### 4) Schema DB documentato vs migration reali
**Valutazione: parzialmente coerente.**

Le migration reali creano:
- `signals`
- `events`
- `warnings`
- `trades`
- `raw_messages`
- `parse_results`

La documentazione DB descrive anche tabelle target non ancora migrate:
- `update_matches`
- `trade_state_events`
- `resolution_logs`

### 5) Harness parser_test
**Valutazione: coerente lato setup, con gap nei test.**

`parser_test/.env.example` e presente nel repository, quindi il setup documentato e eseguibile.
Resta pero un mismatch applicativo: sul branch corrente alcuni test TA non sono verdi.

---

## Rischi residui

1. Chi legge i documenti execution, risk, bot o lifecycle puo assumere feature runtime non ancora implementate.
2. Chi legge `DB_SCHEMA.md` puo assumere tabelle non ancora migrate nel database reale.
3. La documentazione puo divergere rapidamente se non viene aggiornata insieme ai TODO dei moduli runtime.
4. I test TA oggi segnalano un disallineamento tra comportamento atteso e comportamento reale.

---

## Follow-up consigliati

1. Mantenere nei documenti execution/bot la dicitura esplicita di specifica target.
2. Mantenere `DB_SCHEMA.md` esplicito su tabelle migrate oggi vs tabelle target.
3. Usare `docs/IMPLEMENTATION_STATUS.md` come indice rapido di stato.
4. Decidere se correggere il parser TA o aggiornare i test per riallineare il comportamento atteso.

---

## Conclusione

La documentazione e utile e in buona parte corretta, ma descrive un mix di:
- parti gia implementate
- parti presenti nel repository ma non nel flusso runtime principale
- parti ancora a livello di design target

Per evitare ambiguita operative, ogni documento deve dichiarare esplicitamente se descrive stato attuale o stato target.
