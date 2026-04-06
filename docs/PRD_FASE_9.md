# PRD Fase 9 - Entry Plan Runtime v1: MARKET + LIMIT per singolo segnale

**Stato:** DESIGN - 2026-03-30
**Dipendenze:** Fase 5-6 live bridge operativo, Fase 7-8 indipendenti

---

## Context

Il bridge live attuale supporta bene:

1. segnali con entry `LIMIT`
2. update runtime (`U_MOVE_STOP`, `U_CLOSE_FULL`, `U_CLOSE_PARTIAL`, `U_CANCEL_PENDING`)
3. tracking DB `signals -> trades -> orders -> events`

Il gap aperto e strutturale:

- il parser distingue correttamente il tipo di entry per singolo segnale
- `signals.entry_json` preserva `type = MARKET | LIMIT`
- Freqtrade usa invece `order_types.entry` come configurazione globale

Questa divergenza genera un comportamento scorretto:

- un segnale `SINGLE_MARKET` puo finire come ordine `LIMIT` pendente
- un segnale misto `E1 MARKET + E2 LIMIT` non puo essere rappresentato dal flusso runtime corrente

---

## Obiettivo

Supportare il piano di ingresso reale del segnale, non un solo order type globale.

Il requisito corretto non e:

- "il trade e market"
- "il trade e limit"

Il requisito corretto e:

- ogni `entry leg` (`E1`, `E2`, `E3`, ...) ha il proprio `order_type`
- il runtime live deve eseguire ogni leg secondo il tipo dichiarato nel segnale

---

## Visione utente

### Caso A - Single market

```text
Segnale:
  E1 MARKET @ current

Runtime atteso:
  dispatch immediato entry MARKET
  trade OPEN
  protective orders creati come oggi
```

### Caso B - Single limit

```text
Segnale:
  E1 LIMIT @ 614.36

Runtime atteso:
  continua a usare il flusso strategy standard
  order entry pendente finche non viene fillato
```

### Caso C - Mixed entry plan

```text
Segnale:
  E1 MARKET 50%
  E2 LIMIT  30%
  E3 LIMIT  20%

Runtime atteso:
  E1 eseguito subito via dispatcher market
  E2/E3 restano gambe di averaging future
  il trade salva stato entry legs gia eseguite / ancora pendenti
```

---

## Principio architetturale

La Fase 9 introduce il concetto di **entry execution per leg**.

Il runtime non decide piu l'entry solo da:

- `strategy.order_types["entry"]`

ma da:

- `signals.entry_json[n].type`
- `signals.entry_json[n].price`
- `operational_signals.entry_split_json`
- stato corrente delle gambe gia eseguite

---

## Scope Fase 9

### In scope

1. supporto live per `E1 MARKET`
2. mantenimento del supporto esistente per `E1 LIMIT`
3. tracking esplicito dello stato delle entry legs
4. predisposizione per averaging successivi misti
5. idempotenza del dispatch market
6. audit trail DB degli entry dispatch / mismatch / fill

### Out of scope

1. refactor completo del motore Freqtrade
2. sostituzione totale della strategy live
3. supporto full a `E2/E3 MARKET` nello stesso step
4. ordini complessi exchange-native per averaging multipli gia piazzati in anticipo

---

## Decisione tecnica

### Runtime split

Il runtime live viene diviso in due percorsi:

1. **Strategy entry path**
   - usa `populate_entry_trend()`
   - gestisce solo segnali il cui primo leg e `LIMIT`

2. **Market entry dispatcher path**
   - legge dal DB i segnali `PENDING` con primo leg `MARKET`
   - dispatcha l'entry immediatamente
   - riusa i callback esistenti per persistenza DB

### Safety rule

Se un segnale richiede `MARKET` ma il runtime sta ancora tentando di passare dal path `LIMIT`, l'entry deve essere rifiutata esplicitamente.

Questo guard e gia stato introdotto:

- evento `ENTRY_ORDER_TYPE_MISMATCH`
- niente ordine pendente fuorviante

---

## Modello dati

### Stato minimo delle entry legs

Fase 9 introduce tracking esplicito delle gambe di ingresso in `trades.meta_json`.

Struttura minima:

```json
{
  "entry_legs": [
    {
      "entry_id": "E1",
      "sequence": 1,
      "order_type": "MARKET",
      "price": 614.36,
      "split": 0.5,
      "status": "FILLED"
    },
    {
      "entry_id": "E2",
      "sequence": 2,
      "order_type": "LIMIT",
      "price": 610.0,
      "split": 0.3,
      "status": "PENDING"
    }
  ]
}
```

### Regole

- `FILLED`: leg gia eseguita
- `PENDING`: leg futura ancora non eseguita
- `CANCELLED`: leg rimossa da update o invalidazione
- `SKIPPED`: leg non applicabile / non usata

---

## Step 28 - Normalizer: entry legs runtime

**Deliverable:** aggiornamento `src/execution/freqtrade_normalizer.py`

### Nuovi modelli

```python
@dataclass(slots=True, frozen=True)
class FreqtradeEntryLeg:
    entry_id: str
    sequence: int
    order_type: str         # MARKET | LIMIT
    price: float | None
    split: float
    role: str | None = None
```

### Nuove proprieta in `FreqtradeSignalContext`

- `entry_legs: tuple[FreqtradeEntryLeg, ...]`
- `first_entry_leg: FreqtradeEntryLeg | None`
- `market_entry_required: bool`
- `limit_entry_required: bool`

### Regole

1. costruire le legs da `signals.entry_json`
2. usare `operational_signals.entry_split_json` se presente
3. se split non presente:
   - 1 leg -> `1.0`
   - N legs -> split uniforme
4. `first_entry_order_type` resta per backward compatibility ma deriva da `first_entry_leg`

**Test:** estendere `src/execution/tests/test_freqtrade_bridge.py`

---

## Step 29 - Strategy path: LIMIT only

**Deliverable:** aggiornamento `freqtrade/user_data/strategies/SignalBridgeStrategy.py`

### Modifica

`populate_entry_trend()` deve pubblicare segnali solo quando:

- `context.first_entry_order_type == "LIMIT"`

Se il primo leg e `MARKET`, la strategy standard non deve emettere `enter_long/enter_short`.

### Motivazione

Evitiamo doppio dispatch:

- strategy path per `LIMIT`
- dispatcher path per `MARKET`

### Guard

`confirm_trade_entry()` continua a rifiutare:

- `signal first leg = MARKET`
- `runtime order_type != MARKET`

**Test:** aggiungere test che `populate_entry_trend()` ignora segnali `MARKET`

---

## Step 30 - Market Entry Dispatcher

**Deliverable:** nuovo `src/execution/market_entry_dispatcher.py`

### Responsabilita

1. leggere dal DB i segnali `PENDING` con primo leg `MARKET`
2. applicare controlli di idempotenza
3. creare il dispatch entry
4. persistere risultati usando callback e tabelle esistenti

### API proposta

```python
class MarketEntryDispatcher:
    def __init__(self, *, db_path: str, gateway: ExchangeGateway | None = None) -> None: ...

    def dispatch_pending_market_entries(self) -> list[dict[str, Any]]:
        ...
```

### Query minima

Un segnale e candidabile se:

- `signals.status = 'PENDING'`
- nessuna row in `trades` per `attempt_key`
- nessun ordine `ENTRY` attivo per `attempt_key`
- `FreqtradeSignalContext.first_entry_order_type == "MARKET"`
- `context.is_executable == True`

### Risultato minimo

Per ogni tentativo:

- `MARKET_ENTRY_DISPATCHED`
- `ENTRY_FILLED` se l'entry risulta eseguita
- warning / event in caso di errore

---

## Step 31 - Persistenza idempotente

**Deliverable:** aggiornamento callback / metadata trade

### Regola di idempotenza

Un leg market non deve essere dispatchato due volte.

Controlli minimi:

1. no `trades` row per `attempt_key`
2. no `ENTRY` order attivo per `attempt_key`
3. no event `MARKET_ENTRY_DISPATCHED` gia presente per lo stesso `attempt_key` e stesso `entry_id`

### Audit trail

Nuovi eventi possibili:

- `MARKET_ENTRY_DISPATCHED`
- `MARKET_ENTRY_DISPATCH_FAILED`
- `ENTRY_ORDER_TYPE_MISMATCH`

---

## Step 32 - Averaging mixed plans

**Deliverable:** predisposizione runtime per `E2/E3 LIMIT`

### Scope minimo di Fase 9

Non serve ancora piazzare subito tutti gli averaging limit come veri ordini exchange.
Serve pero:

1. conservare il piano completo delle legs
2. marcare `E1` come `FILLED`
3. lasciare `E2/E3` come `PENDING`
4. rendere leggibile il piano da `adjust_trade_position()` o dal futuro manager averaging

### Scelta

Fase 9 salva il piano e prepara l'handoff.
L'esecuzione piena delle legs successive puo essere completata nella fase successiva.

---

## Contratto runtime

| Caso | Runtime atteso |
|---|---|
| `E1 LIMIT` | strategy standard |
| `E1 MARKET` | dispatcher custom |
| `E1 MARKET + E2 LIMIT` | dispatcher per `E1`, `E2` resta leg pending |
| `E1 LIMIT + E2 LIMIT` | comportamento attuale |
| `E1 MARKET` ma runtime prova `LIMIT` | reject con `ENTRY_ORDER_TYPE_MISMATCH` |

---

## File impattati

### Nuovi

- `src/execution/market_entry_dispatcher.py`

### Modificati

- `src/execution/freqtrade_normalizer.py`
- `freqtrade/user_data/strategies/SignalBridgeStrategy.py`
- `src/execution/freqtrade_callback.py`
- `src/execution/tests/test_freqtrade_bridge.py`
- eventuale `src/execution/tests/test_market_entry_dispatcher.py`

---

## Piano test

### Unit

1. normalizer costruisce correttamente `entry_legs`
2. split leg corretto da `entry_split_json`
3. `first_entry_order_type` derivato correttamente

### Strategy

1. `populate_entry_trend()` include solo `LIMIT`
2. `MARKET` non produce entry signal standard
3. mismatch `MARKET` vs `LIMIT` genera reject + event

### Dispatcher

1. dispatch di `E1 MARKET` crea fill + trade
2. secondo pass non ridispatcha lo stesso `attempt_key`
3. mixed plan salva `E2/E3` come `PENDING`

### E2E

1. segnale `SINGLE_MARKET`
2. segnale `SINGLE_LIMIT`
3. segnale `MARKET + LIMIT averaging`

---

## Rischi

1. doppio dispatch se strategy e dispatcher leggono lo stesso segnale
2. inconsistenza tra fill exchange e persistenza DB
3. gestione incompleta degli averaging mixed plans se si tenta di fare troppo nella prima iterazione

### Mitigazioni

1. strategy limit-only
2. idempotenza forte su `attempt_key` + event
3. Fase 9 limita lo scope a `E1 MARKET` + piano gambe future

---

## Exit criteria

Fase 9 e completata quando:

1. un segnale `SINGLE_MARKET` non produce piu ordine limit pendente
2. `SINGLE_MARKET` genera trade/eventi coerenti nel DB
3. `SINGLE_LIMIT` continua a funzionare senza regressioni
4. un segnale misto `E1 MARKET + E2 LIMIT` conserva il piano delle gambe
5. i test unit e integrazione della nuova catena sono verdi

