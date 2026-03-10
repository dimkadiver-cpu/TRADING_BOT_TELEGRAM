# DOCUMENTATION COHERENCE AUDIT

## Obiettivo
Verificare la coerenza tra documentazione e stato reale del codice nel repository.

## Data
Audit eseguito su branch corrente.

---

## Esito sintetico

Stato complessivo: **parzialmente coerente**.

- **Area parser + ingestion + persistenza parse**: buona coerenza.
- **Area execution/risk/state machine/bot commands**: documentazione più avanzata dell'implementazione reale (moduli ancora TODO).
- **Area parser_test**: piccola incoerenza operativa (README richiede un file `.env.example` non presente).

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

---

### 2) Contratto canonico eventi e normalizzazione
**Valutazione: coerente.**

I tipi evento canonici documentati sono allineati alla normalizzazione implementata:
- `NEW_SIGNAL`, `UPDATE`, `CANCEL_PENDING`, `MOVE_STOP`, `TAKE_PROFIT`, `CLOSE_POSITION`, `INFO_ONLY`, `SETUP_INCOMPLETE`, `INVALID`.

Sono presenti anche validazioni non bloccanti e warning, in linea con i documenti tecnici.

---

### 3) Execution, risk engine, planner, state machine, bot commands
**Valutazione: parzialmente coerente (documentazione in anticipo sul codice).**

I documenti di dominio sono utili e ben strutturati, ma nel codice i moduli sono ancora placeholder/TODO:
- `src/execution/risk_gate.py`
- `src/execution/planner.py`
- `src/execution/state_machine.py`
- `src/telegram/bot.py`

Quindi la documentazione va interpretata come **target design** più che come comportamento già disponibile end-to-end.

---

### 4) Harness parser_test
**Valutazione: quasi coerente con una discrepanza puntuale.**

`parser_test/README.md` indica di copiare `parser_test/.env.example`, ma il file non è tracciato nel repository.

Impatto:
- basso (gli script possono usare variabili ambiente anche senza template)
- ma può rallentare onboarding e setup rapido.

---

## Rischi residui

1. **Rischio di aspettative errate operative**: chi legge `BOT_COMMANDS.md`, `RISK_ENGINE.md` o `TRADE_STATE_MACHINE.md` può assumere feature runtime non ancora implementate.
2. **Rischio onboarding parser_test**: istruzione iniziale non eseguibile alla lettera per assenza `.env.example`.
3. **Rischio drift documentale**: roadmap e docs architetturali possono divergere rapidamente se non aggiornati insieme ai TODO in `src/execution/*`.

---

## Follow-up consigliati

1. Aggiungere etichetta chiara nei documenti execution/bot: **"Spec di progetto (non ancora implementata runtime)"**.
2. Creare `parser_test/.env.example` minimale coerente con gli script.
3. Introdurre un breve file `docs/IMPLEMENTATION_STATUS.md` con matrice `documentato vs implementato` per modulo.

---

## Conclusione
La documentazione è in gran parte utile e strutturalmente corretta, ma al momento rappresenta un mix di:
- parti già implementate (ingestion + parser + persistenza)
- parti ancora a livello di design (execution/risk/state machine/bot).

Per evitare ambiguità operative conviene esplicitare in ogni doc se descrive **stato attuale** o **stato target**.
