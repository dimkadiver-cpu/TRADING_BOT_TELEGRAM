# ROADMAP

## Phase status
- Fase 0: COMPLETATA
- Fase 1: COMPLETATA
- Fase 2: IN CORSO
- Fase 3: VALIDATA
- Fase 4: IN CORSO AVANZATA
- Fase 5: IN ANALISI
- Fase 6: IN ANALISI
- Fase 7: NON INIZIATA
- Fase 8: NON INIZIATA
- Fase 9: IN ANALISI
- Fase 10: NON INIZIATA
- Fase 11: NON INIZIATA
- Fase 12: NON INIZIATA
- Fase 13: NON INIZIATA

---

## Fase 0. Fondazioni del repo
**Stato:** COMPLETATA

Completato:
- struttura repo riallineata
- documentazione principale ordinata
- file guida per sviluppo assistito

---

## Fase 1. Modello dati interno
**Stato:** COMPLETATA

Completato:
- definite le entità principali:
  - Raw Message
  - Parse Result
  - Trade
  - Update Match
- chiarita la separazione tra:
  - sorgente Telegram
  - trader dichiarato
  - trader risolto
  - valori raw
  - valori finali

---

## Fase 2. Database e persistenza
**Stato:** IN CORSO

Già definito:
- schema logico minimo
- tabelle principali:
  - `raw_messages`
  - `parse_results`
  - `trades`
  - `update_matches`
  - `trade_state_events`
  - `resolution_logs` consigliata

Ancora da completare:
- allineamento pieno dello schema reale ai docs
- persistenza completa di `parse_results`
- campi linkage e trader resolution persistiti in modo definitivo

---

## Fase 3. Telegram ingestion
**Stato:** VALIDATA

Validato in test reale:
- filtro `allowed_chat_ids`
- persistenza `raw_messages`
- deduplica minima
- mapping sorgente
- comportamento runtime corretto con `.env`

Nota importante:
il mapping sorgente resta utile per:
- filtrare il canale corretto
- identificare il contenitore Telegram
- eventuale fallback solo per sorgenti davvero mono-trader

In un canale multi-trader, la sorgente **non** rappresenta da sola il trader effettivo.

---

## Fase 4. Parser minimo operativo
**Stato:** IN CORSO AVANZATA

Obiettivo:
trasformare ogni `raw_message` in un `parse_result` minimo, prudente e persistito.

Già avviato / parzialmente implementato:
- eligibility pre-check
- effective trader resolution iniziale
- reply inheritance iniziale
- strong-link pre-check per update brevi
- distinzione preliminare tra messaggi eligibili, review-only, unknown-trader

Ancora da completare:
- classificazione minima:
  - `NEW_SIGNAL`
  - `SETUP_INCOMPLETE`
  - `UPDATE`
  - `INFO_ONLY`
  - `UNCLASSIFIED`
- estrazione campi base:
  - symbol
  - direction
  - entry
  - stop
  - target list
  - leverage hint
  - risk hint
  - risky flag
- validazione minima `NEW_SIGNAL` vs `SETUP_INCOMPLETE`
- persistenza completa `parse_results`
- supporto linkage minimo tramite reply e campi linkage persistiti

Checkpoint principali della fase:
- [ ] eligibility rules definite e persistite
- [ ] trader tag extraction implementata
- [ ] trader resolution con `DIRECT_TAG` / `REPLY_INHERIT` stabile
- [ ] update brevi trattati con strong-link prudente
- [ ] parse result persistito
- [ ] admin/stats/service esclusi dal flusso operativo

---

## Fase 5. Matching update -> trade
**Stato:** IN ANALISI

Obiettivo:
collegare gli update al trade corretto solo con linkage affidabile.

Metodi previsti:
- `REPLY`
- `MESSAGE_LINK`
- `EXPLICIT_MESSAGE_ID`
- `CONTEXT_FALLBACK` solo con prudenza

Regola forte:
gli update brevi non devono auto-applicarsi con puro contesto debole.

---

## Fase 6. Resolver policy
**Stato:** IN ANALISI

Obiettivo:
risolvere valori finali usando:
- hint da messaggio
- regole trader
- regole globali

Parametri principali:
- leverage
- risk
- TP allocation
- motivazione decisionale

---

## Fase 7. Level normalizer
**Stato:** NON INIZIATA

Obiettivo:
applicare:
- rounding
- Number Theory
- precisione
- normalizzazione livelli

---

## Fase 8. Trade planner
**Stato:** NON INIZIATA

Obiettivo:
costruire il piano operativo finale a partire da:
- parse result
- linkage
- policy
- normalizer

---

## Fase 9. State machine reale
**Stato:** IN ANALISI

Obiettivo:
governare il lifecycle del trade in modo coerente e verificabile.

---

## Fase 10. Exchange integration
**Stato:** NON INIZIATA

## Fase 11. Reconciliation e sicurezza
**Stato:** NON INIZIATA

## Fase 12. Bot comandi e monitoraggio
**Stato:** NON INIZIATA

## Fase 13. Test, simulazione, hardening
**Stato:** NON INIZIATA

---

## Priorità attuale
Prossimo target:
completare la Fase 4 minima con:
- classificazione messaggi
- estrazione campi base
- validazione setup
- persistenza `parse_results`

Solo dopo:
- matching update completo
- planner
- exchange
