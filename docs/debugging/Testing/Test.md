# Piano di test live

Obiettivo: verificare, su conto demo, che ogni comando venga eseguito correttamente dal runtime e che lo stato finale del DB/exchange sia coerente con l'azione richiesta.

## Regole del test

- Esegui un solo caso alla volta.
- Tieni aperta una sola chain per simbolo.
- Usa size minima.
- Non andare avanti se compare `REVIEW_REQUIRED` o `FAILED` senza che il caso lo preveda.
- Il segnale di verità è la combinazione di:
  - log runtime;
  - `db/parser.sqlite3`;
  - `db/ops.sqlite3`;
  - stato ordine su exchange demo.

## Preflight

- `python main.py` parte senza errori.
- Il log mostra listener, parser ed execution gateway attivi.
- `config/channels.yaml`, `config/operation_config.yaml`, `config/execution.yaml` sono coerenti con il trader da testare.
- Non ci sono chain aperte residue che possano interferire.

Query base:

```sql
SELECT trade_chain_id, trader_id, symbol, side, lifecycle_state, created_at
FROM ops_trade_chains
ORDER BY created_at DESC
LIMIT 10;

SELECT command_id, trade_chain_id, command_type, status, created_at, updated_at
FROM ops_execution_commands
ORDER BY created_at DESC
LIMIT 20;

SELECT event_id, trade_chain_id, event_type, next_state, created_at
FROM ops_lifecycle_events
ORDER BY created_at DESC
LIMIT 20;

SELECT exchange_event_id, trade_chain_id, event_type, processing_status, received_at
FROM ops_exchange_events
ORDER BY received_at DESC
LIMIT 20;
```

## Criteri di pass/fail

- Passa se il comando richiesto compare con il `command_type` atteso e raggiunge uno stato coerente: `PENDING`, `SENT`, `ACK`, `WAITING_POSITION`, `DONE`.
- Passa se la chain finale coincide con l'azione eseguita: `OPEN`, `PARTIALLY_CLOSED`, `CLOSED`, `CANCELLED`, `EXPIRED`.
- Fallisce se un comando resta bloccato senza motivo, viene duplicato, o porta a uno stato non atteso.
- Fallisce se l'exchange demo mostra una side, qty o chiusura diversa da quella richiesta.

## Sequenza consigliata

### Caso 1: `Limit + SL + TP`

Verifica principale del flusso.

- [ ] Inviare segnale `LIMIT`
- [ ] Verificare creazione chain in `ops_trade_chains`
- [ ] Verificare `PLACE_ENTRY`
- [ ] Verificare `PLACE_PROTECTIVE_STOP` e `PLACE_TAKE_PROFIT` se previsti dalla strategia
- [ ] Verificare apertura ordine su exchange demo
- [ ] Verificare `CANCEL_PENDING_ENTRY` quando cancelli l'entry pendente
- [ ] Verificare chiusura parziale con `CLOSE_PARTIAL`
- [ ] Verificare chiusura totale con `CLOSE_FULL`
- [ ] Verificare `MOVE_STOP_TO_BREAKEVEN`
- [ ] Verificare `MOVE_STOP`

Atteso:

- `ops_execution_commands` mostra i comandi nell'ordine corretto.
- `ops_exchange_events` registra i fill/cancel/close coerenti.
- La chain termina nello stato corretto per l'ultima azione eseguita.

### Caso 1_1: `2 Limit + SL + TP`

Verifica multi-leg e gestione ordini non fillati.

- [ ] Inviare segnale con due entry limit
- [ ] Verificare che entrambe le entry siano create
- [ ] Verificare cancellazione del primo ordine non fillato
- [ ] Verificare cancellazione del secondo ordine non fillato
- [ ] Verificare modifica del primo ordine al market, se prevista dal flusso
- [ ] Verificare apertura del secondo ordine
- [ ] Verificare chiusura totale
- [ ] Verificare chiusura parziale
- [ ] Verificare `MOVE_STOP_TO_BREAKEVEN`
- [ ] Verificare `MOVE_STOP`

Atteso:

- Nessuna duplicazione di leg.
- Le cancellazioni riguardano solo gli ordini pendenti.
- Lo stato finale della chain resta coerente con la leg aperta.

### Caso 2: `Market + SL + TP/TP`

Verifica del path market.

- [ ] Inviare segnale `MARKET`
- [ ] Verificare `PLACE_ENTRY`
- [ ] Verificare piazzamento corretto di SL e TP
- [ ] Verificare apertura immediata dell'ordine
- [ ] Verificare chiusura totale
- [ ] Verificare chiusura parziale
- [ ] Verificare `MOVE_STOP_TO_BREAKEVEN`
- [ ] Verificare `MOVE_STOP`

Atteso:

- L'entry non resta bloccata in pending.
- I protective orders risultano coerenti con il piano del trader.

### Caso 2_1: `Market + Limit + SL + TP`

Verifica mista market + limit.

- [ ] Inviare setup misto
- [ ] Verificare cancellazione ordini non fillati
- [ ] Verificare cancellazione del secondo ordine non fillato
- [ ] Verificare eventuale modifica del primo ordine al market
- [ ] Verificare apertura del secondo ordine
- [ ] Verificare chiusura totale
- [ ] Verificare chiusura parziale
- [ ] Verificare `MOVE_STOP_TO_BREAKEVEN`
- [ ] Verificare `MOVE_STOP`

Atteso:

- I comandi seguono la struttura del setup misto senza generare ordini extra.

## Checklist finale

- [ ] Caso 1 completato senza `FAILED` inattesi
- [ ] Caso 1_1 completato senza duplicati o ordini residui
- [ ] Caso 2 completato con entry market corretta
- [ ] Caso 2_1 completato con routing corretto dei comandi
- [ ] Nessuna chain rimasta aperta per errore
- [ ] Nessun comando rimasto in uno stato incoerente
- [ ] Log e DB coerenti con l'ultima azione eseguita

## Nota pratica

Se un caso fallisce, non continuare con il successivo. Prima bisogna capire quale layer ha rotto il contratto:

- parser;
- enrichment;
- lifecycle;
- execution gateway;
- reconciliation/fill sync.
