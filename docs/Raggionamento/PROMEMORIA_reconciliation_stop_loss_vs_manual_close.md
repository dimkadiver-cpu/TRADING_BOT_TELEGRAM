# Promemoria - Reconciliation, stop loss e falso manual close

## Contesto

Durante la verifica del caso live:

```text
BTCUSDT LONG
db: C:\TeleSignalBot\db\ops.sqlite3
```

e emerso che la notifica finale:

```text
Close reason: MANUAL_CLOSE
Source: position_reconciliation
```

non prova una chiusura manuale reale lato exchange.

Indica invece che il runtime non ha ricevuto o non ha consolidato
un evento di chiusura specifico (`SL_FILLED` o `TP_FILLED`) e ha chiuso
la chain solo tramite fallback di riconciliazione posizione.

---

## Caso osservato nel DB

La chain reale osservata e:

```text
trade_chain_id = 1
symbol         = BTCUSDT
side           = LONG
lifecycle      = CLOSED
```

### Stato chain consolidato

In `ops_trade_chains` risultano:

```text
entry_avg_price     = 65590.08353960395
expected_stop_price = 65471.3
filled_entry_qty    = 1.616
open_position_qty   = 0.0
closed_position_qty = 1.616
cumulative_gross_pnl = 0.0
cumulative_fees      = 44.5723513
```

Quindi:

```text
lo stop loss atteso esisteva
la posizione e stata effettivamente azzerata
ma il sistema non ha un fill price finale
```

### Eventi exchange consolidati

In `ops_exchange_events` per la chain `#1` risultano:

```text
ENTRY_FILLED
ENTRY_FILLED
CLOSE_FULL_FILLED
```

L'evento finale e:

```json
{
  "filled_qty": 1.616,
  "fill_price": null,
  "source": "position_reconciliation"
}
```

Quindi:

```text
nessun SL_FILLED
nessun TP_FILLED
nessun prezzo finale noto
chiusura ricostruita solo dal fatto che la posizione non esiste piu
```

### Evento lifecycle consolidato

In `ops_lifecycle_events` l'evento finale e:

```text
event_type = CLOSE_FULL_FILLED
previous_state = OPEN
next_state = CLOSED
payload.source = position_reconciliation
```

Questa e la vera origine della notifica finale.

---

## Evidenza raw exchange

In `exchange_raw_events` risultano chiaramente:

```text
- due ENTRY_FILLED corretti
- ordine TP creato correttamente
- ordine SL creato correttamente
- snapshot posizione aperta con:
    position_take_profit = 66289.7
    position_stop_loss   = 65471.3
```

In particolare esiste un raw order di stop:

```text
create_type     = CreateByStopLoss
stop_order_type = StopLoss
```

Ma NON esiste nessun raw finale classificato come:

```text
SL_FILLED
TP_FILLED
MANUAL_CLOSE_FULL
MANUAL_CLOSE_PARTIAL
```

Quindi il sistema ha visto:

```text
lo SL e stato creato
ma non ha visto l'evento di esecuzione finale
```

---

## Come il sistema distingue oggi i casi

### Caso 1 - vero stop loss riconosciuto

Il classifier produce `SL_FILLED` quando riceve un evento execution con
segnali deterministici Bybit:

```text
createType in {CreateByStopLoss, CreateByPartialStopLoss}
oppure
stopOrderType in {StopLoss, PartialStopLoss}
```

Riferimento:

```text
src/runtime_v2/execution_gateway/event_ingest/classifier.py
```

Se accade questo:

```text
ops_exchange_events -> SL_FILLED
ops_lifecycle_events -> SL_FILLED
outbox -> notification_type = SL_FILLED
clean_log -> Close reason: STOP_LOSS
```

### Caso 2 - chiusura ricostruita da riconciliazione posizione

Se il bot trova:

```text
chain OPEN/PARTIALLY_CLOSED nel DB
ma qty exchange = 0
```

allora `run_position_reconciliation()` inserisce sempre:

```text
event_type = CLOSE_FULL_FILLED
source     = position_reconciliation
```

senza distinguere se la causa reale sia:

```text
- stop loss
- take profit
- close manuale
- altra chiusura esterna
```

Riferimento:

```text
src/runtime_v2/execution_gateway/event_sync.py
```

### Caso 3 - chiusura manuale vera da raw execution

Il classifier usa `MANUAL_CLOSE_FULL` o `MANUAL_CLOSE_PARTIAL` solo quando vede
un evento execution con:

```text
createType = CreateByUser
orderLinkId assente
closedSize > 0
```

e poi distingue full vs partial in base a `posQty`.

Questo NON e il caso osservato su BTCUSDT.

---

## Punto critico architetturale

La riconciliazione posizione oggi e un fallback troppo generico.

Regola attuale implicita:

```text
se la posizione e sparita ma non ho il fill specifico
allora genero CLOSE_FULL_FILLED
```

Problema:

```text
il control plane proietta CLOSE_FULL_FILLED come POSITION_CLOSED
e usa MANUAL_CLOSE come default se manca close_reason
```

Quindi il testo finale:

```text
Close reason: MANUAL_CLOSE
```

in questo scenario significa in pratica:

```text
close reason unknown after reconciliation fallback
```

non:

```text
chiusura manuale confermata
```

---

## Root cause del caso osservato

La root cause verificata e:

```text
evento finale di chiusura non presente nel path consolidato
-> nessun SL_FILLED / TP_FILLED disponibile
-> la posizione viene trovata a zero solo dal polling
-> la chain viene chiusa come CLOSE_FULL_FILLED da position_reconciliation
-> il formatter notifica MANUAL_CLOSE per default
```

Questo spiega anche il risultato economico finale:

```text
Gross PnL = 0.0
Fees      = -44.5723513
Net       = -44.5723513
```

Senza `fill_price` finale il sistema non puo calcolare il lordo della chiusura,
quindi resta solo il totale fee gia accumulato sulle entry.

---

## Conclusione corretta da preservare

Per casi come questo, la frase corretta non e:

```text
la posizione e stata chiusa manualmente
```

ma:

```text
la posizione e stata rilevata chiusa dalla reconciliation
senza evidenza sufficiente per distinguerne la causa reale
```

---

## Gap specifico del runtime attuale

Oggi esiste una asimmetria:

```text
trade-based reconciliation recupera TP_FILLED
position reconciliation chiude genericamente la chain
```

ma non esiste un percorso equivalente che dica:

```text
probabile stop loss
```

quando:

```text
- la posizione sparisce
- esisteva uno SL attached noto
- non risultano trade TP
- non esistono eventi raw di chiusura meglio classificati
```

---

## Direzione di fix raccomandata

### Obiettivo minimo

Evitare che il fallback venga mostrato come `MANUAL_CLOSE`
quando la causa reale e semplicemente sconosciuta.

### Opzione pragmatica

Introdurre una distinzione lato payload/notifica:

```text
UNKNOWN_RECONCILIATION_CLOSE
oppure
RECONCILIATION_CLOSE
```

al posto di:

```text
MANUAL_CLOSE
```

quando l'evento nasce da:

```text
source = position_reconciliation
```

### Opzione migliore

Arricchire `run_position_reconciliation()` con inferenza causale minima:

```text
1. se esiste evidenza TP -> TP_FILLED
2. se esiste evidenza SL attached e nessuna evidenza TP -> PROBABLE_STOP_LOSS
3. se esiste evidenza manual/esterna -> MANUAL_CLOSE
4. altrimenti -> RECONCILIATION_CLOSE_UNKNOWN
```

### Vincolo importante

Non bisogna spacciare per `STOP_LOSS` certo un caso che e solo inferito.

Meglio:

```text
PROBABLE_STOP_LOSS
```

che:

```text
STOP_LOSS
```

senza prova exchange sufficiente.

---

## Regola pratica da ricordare

Nel runtime attuale:

```text
Source: position_reconciliation
```

piu:

```text
Close reason: MANUAL_CLOSE
```

non significa automaticamente:

```text
utente ha chiuso manualmente la posizione
```

Significa:

```text
il sistema ha perso il motivo preciso della chiusura
e ha chiuso la chain per assenza posizione
```

---

## Acceptance criteria di un fix futuro

Il problema puo considerarsi chiuso quando:

1.

```text
una chiusura derivata solo da reconciliation non viene piu etichettata
come manuale per default
```

2.

```text
la UI/log distingue tra:
- stop loss certo
- stop loss probabile
- close manuale certo
- close reconciliation senza causa nota
```

3.

```text
il payload finale conserva esplicitamente il grado di certezza della causa
```

4.

```text
il calcolo PnL finale non resta ambiguo senza spiegare che manca il fill price finale
```

---

## Sintesi finale

Il caso BTCUSDT verificato mostra un problema preciso:

```text
lo stop loss era presente
la posizione e stata trovata chiusa
ma il runtime non ha ricevuto o non ha consolidato il fill finale
```

Per questo motivo:

```text
la chain e stata chiusa da position_reconciliation
e il control plane ha mostrato MANUAL_CLOSE come fallback semantico
```

Conclusione netta:

```text
MANUAL_CLOSE in questo caso e una etichetta di fallback
non una prova di chiusura manuale reale
```
