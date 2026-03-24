# Fase 3 — Router / Pre-parser

## Obiettivo

Documentare lo stato reale della Fase 3 nel repository:
- cosa è già completato
- cosa manca per considerarla chiusa
- quali rischi restano
- quale criterio usare per dichiararla pronta al passaggio stabile verso la Fase 4

---

## Stato sintetico

La Fase 3 è **implementata a livello architetturale**, ma non è ancora completamente **chiusa come robustezza operativa**.

In pratica:
- la logica Router / Pre-parser esiste ed è integrata nel flusso
- la reply-chain transitiva è presente
- la review queue è presente
- il routing verso parser e persistenza dei risultati è presente
- restano configurazione live, test end-to-end più completi e hardening dei casi multi-trader più sporchi

---

## Completato

### 1. Blacklist

Il Router esegue blacklist:
- globale da `channels.yaml`
- specifica per canale

Esito:
- `processing_status = blacklisted`
- niente passaggio al parser
- messaggio preservato in `raw_messages`

Riferimenti:
- `src/telegram/router.py`
- `src/telegram/tests/test_router.py`

### 2. Risoluzione trader

La risoluzione trader è implementata con priorità:
1. alias/tag nel testo corrente
2. reply-chain transitiva
3. fallback da mapping canale

La reply-chain:
- risale più livelli
- usa depth limit
- protegge da loop
- usa `source_trader_id` come segnale più forte
- può usare alias nel testo storico

Riferimenti:
- `src/telegram/effective_trader.py`
- `src/telegram/tests/test_reply_chain.py`

### 3. Review queue

Quando il trader non è risolto:
- `processing_status = review`
- viene creata un'entry in `review_queue`
- il messaggio non viene perso

Riferimenti:
- `src/storage/review_queue.py`
- `src/telegram/router.py`
- `src/telegram/tests/test_router.py`

### 4. Filtro attivi/inattivi

Se il trader/canale è inattivo:
- il messaggio viene preservato
- il flusso si ferma senza parsing operativo
- lo storico resta disponibile per replay futuro

Riferimenti:
- `src/telegram/router.py`
- `src/telegram/tests/test_router.py`

### 5. Costruzione ParserContext

Il Router costruisce `ParserContext` con:
- trader risolto
- ids messaggio/reply
- `reply_raw_text`
- `hashtags`
- `extracted_links`

Questo conferma che il parser non dipende direttamente dal DB per ricostruire il contesto minimo.

Riferimenti:
- `src/telegram/router.py`
- `src/telegram/tests/test_router.py`

### 6. Chiamata parser e persistenza parse result

Il Router:
- seleziona il parser del trader
- esegue `parse_message(...)`
- valida il risultato
- salva in `parse_results`
- aggiorna `processing_status`

Gestisce anche il caso di eccezione parser con:
- `processing_status = failed`
- logging dell'errore

Riferimenti:
- `src/telegram/router.py`
- `src/validation/coherence.py`
- `src/telegram/tests/test_router.py`

---

## Gap residui

### 1. Configurazione live non pronta

`config/channels.yaml` esiste, ma oggi contiene `channels: []`.

Conseguenza:
- la Fase 3 è pronta come codice
- non è ancora pronta come configurazione operativa reale

### 2. Casi multi-trader ancora sensibili al contesto incompleto

La reply-chain transitiva esiste, ma non può risolvere casi dove:
- il parent storico non è nel DB
- il segnale originario è ambiguo
- il contesto reply è spezzato o rumoroso

Conseguenza:
- alcuni update brevi possono finire ancora in `review`

### 3. Copertura end-to-end limitata ✓ PARZIALMENTE CHIUSO

Aggiunto `src/telegram/tests/test_router_integration.py` (2026-03-24) — 4 scenari con store reali:
- blacklisted → status nel DB verificato
- unresolved trader → review_queue verificata
- parse OK (parser reale trader_3) → parse_results e status verificati
- eccezione persistence → status=failed verificato

Rimane aperta la validazione su backlog reale (richiede dati).

### 4. Confini semantici delicati

La Fase 3 si trova al confine tra:
- eligibility
- effective trader resolution
- routing
- persistence degli stati

Qui anche piccoli cambiamenti possono alterare il destino del messaggio:
- `done`
- `review`
- `failed`
- `blacklisted`

---

## Rischi principali

1. Un aggiornamento breve in canale multi-trader può andare in `review` anche se umanamente il contesto sembra ovvio.

2. Una modifica locale a `eligibility` o `effective_trader` può cambiare il routing senza apparire subito come regressione parser.

3. L'assenza di configurazione reale in `channels.yaml` può far sembrare la Fase 3 più pronta di quanto sia sul piano operativo.

4. I test correnti confermano bene la logica, ma non sostituiscono ancora una validazione runtime completa su backlog realistico.

---

## Semantica processing_status

| Stato | Condizione | Parser chiamato? | parse_results salvato? |
|---|---|---|---|
| `processing` | Transient iniziale (sempre) | — | — |
| `blacklisted` | Testo corrisponde a blacklist globale o per canale | No | No |
| `review` | Trader non risolto (né da alias né da channels.yaml) | No | No |
| `done` (inactive) | Canale marcato `active: false` in channels.yaml | No | No |
| `done` (parsed) | Parse completato senza eccezioni | Sì | Sì |
| `failed` | Qualsiasi eccezione non gestita in `_route_inner` | Sì (tentato) | No |

Note:
- Un messaggio `done` con canale inattivo non ha `parse_results` — lo storico è preservato per replay futuro.
- Un messaggio `failed` può aver già chiamato il parser; il risultato non è affidabile e non viene salvato.
- `review` con `reason=unresolved_trader` indica che il messaggio è recuperabile se il trader viene configurato.

---

## Checklist di chiusura Fase 3

- [x] Esiste una suite integrata affidabile che copre router → parser → persistence (`test_router_integration.py`)
- [x] Il confine tra `blacklisted`, `review`, `failed`, `done` è documentato e verificato (sezione sopra)
- [x] `channels.yaml` valorizzato con almeno un set reale di canali — PifSignal (-1003171748254), multi-trader, trader_a/b/c/d/trader_3
- [ ] Routing live produce esiti coerenti su messaggi reali e replay *(richiede dati live)*
- [ ] Casi multi-trader principali non finiscono in `review` eccessivamente *(richiede misurazione su backlog)*

---

## Valutazione attuale

Valutazione pratica:
- **implementazione**: alta
- **robustezza operativa**: medio-alta (test integrazione aggiunti, confini documentati)
- **prontezza live**: medio-bassa — bloccata da `channels.yaml` vuoto e assenza di dati reali

Lettura consigliata:
- Fase 3 **non è da costruire**
- Fase 3 è da **configurare per il live** — il codice è pronto

---

## Prossimi passi

1. **[utente]** Popolare `config/channels.yaml` con chat_id e trader_id reali
2. **[dopo channels.yaml]** Fare un replay su backlog reale e misurare tasso `review` su multi-trader
3. **[opzionale]** Aggiungere test di integrazione per il path canale inattivo
