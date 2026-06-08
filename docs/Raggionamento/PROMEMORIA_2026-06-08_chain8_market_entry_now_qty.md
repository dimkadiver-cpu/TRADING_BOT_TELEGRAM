# Promemoria - Chain 8, market convert e qty `579.23045 -> 519`

## Punto da fissare

Nel caso della `trade_chain_id = 8`, il testo mostrato nel clean log:

```text
Qty: 519 (planned: 579.23045)
SL qty: 579.23045 -> 519 (adj. to fill)
```

non significa semplicemente:

```text
il sistema ha pianificato 579.23045 anche per la nuova MARKET
e poi l'exchange ne ha riempiti solo 519
```

Il significato reale e piu sottile:

- `579.23045` e la qty pianificata originaria della leg 1 `LIMIT`;
- `519` e la qty realmente fillata dalla nuova entry `MARKET`;
- dopo il passaggio `LIMIT -> MARKET`, la qty viene ricalcolata con logica risk/SL;
- il formatter pero continua a mostrare come `planned_qty` il vecchio numero della leg originaria.

## Stato iniziale della chain

La chain 8 nasce da un segnale `TWO_STEP` su `BEATUSDT LONG`:

- entry 1: `LIMIT` a `4.20318`
- entry 2: `LIMIT` a `4.10712`
- SL: `3.96148`

Nel `risk_snapshot_json` iniziale risultano:

- leg 1 risk amount = `140`
- leg 1 qty = `579.2304509722802`
- leg 2 risk amount = `60`
- leg 2 qty = `411.97473221642343`
- risk amount totale = `200`

Quindi il valore `579.23045` nasce dal piano iniziale della prima entry limit, non da una successiva market conversion.

## Cosa succede con `MARKET_ENTRY_NOW`

La chain riceve un update:

```text
TELEGRAM_UPDATE_ACCEPTED
action = MARKET_ENTRY_NOW
mode   = cancel_subsequent
```

Questo fa due cose:

1. converte la leg 1 da `LIMIT` a `MARKET`;
2. cancella la leg 2.

Nel `plan_state_json` aggiornato infatti risulta:

- leg 1: `entry_type = MARKET`, `qty = null`, `qty_mode = deferred_market`
- leg 2: `status = CANCELLED`

Quindi la chain non sta piu eseguendo il vecchio piano `LIMIT + LIMIT`.
Sta eseguendo un nuovo piano:

- una sola leg market
- con qty risolta al momento dell'invio
- e con cancel delle entry successive

## Come viene scelta la qty della nuova MARKET

Nel path `MARKET_ENTRY_NOW`, con `mode == cancel_subsequent`, il runtime non usa il vecchio rischio della sola leg 1 (`140`).

Usa invece il rischio rimanente/totale della chain:

```text
risk_amount = risk_remaining
```

Nel caso osservato quel valore e `200`.

La replacement command viene quindi costruita con:

- `qty_mode = deferred_market`
- `risk_amount = 200`
- `sl_price = 3.96148`

Poi il gateway risolve la qty reale della MARKET con:

```text
computed_qty = risk_amount / abs(mark_price - sl_price)
```

Questa e la vera logica "per rispettare lo SL":

- si parte dal rischio monetario ammesso;
- si usa la distanza tra mark price e stop;
- da li si ricava la qty teorica da inviare.

## Perche il fill e `519`

Il fill finale osservato e:

- `fill_price = 4.3547`
- `filled_qty = 519`

Questo e coerente con una market entry calcolata sul rischio e poi eseguita con un piccolo scostamento tra:

- mark price usato per stimare la qty;
- prezzo reale di fill.

Con i numeri finali:

```text
rischio realizzato = 519 * (4.3547 - 3.96148) = 204.08118
```

ed e infatti il valore che compare come `risk_already_realized`.

Quindi il `519` non arriva da un semplice "partial fill" della vecchia limit a `579.23045`.
Arriva dalla nuova market convertita, calcolata contro SL e rischio, poi fillata a mercato.

## Perche il clean log e fuorviante

Il formatter `ENTRY_OPENED` prende `planned_qty` dal `risk_snapshot` della leg fillata.

Per la chain 8, quel `risk_snapshot` conserva ancora sulla leg 1:

- qty originaria `579.23045`
- struttura originaria della leg limit

Quindi il clean log confronta:

- vecchia planned qty della leg 1 limit
- nuova filled qty della market convertita

e stampa:

```text
579.23045 -> 519
```

Il confronto e numericamente vero, ma semanticamente mescola due piani diversi:

- piano originario della leg limit
- piano runtime della leg market convertita

## Conclusione corretta

La lettura corretta del caso e:

- si, il passaggio `LIMIT -> MARKET` ha fatto scattare una ricalibrazione qty basata su rischio e SL;
- si, con `cancel_subsequent` il sistema ha usato il rischio residuo/totale della chain, non solo il rischio della leg 1 originaria;
- no, il numero `579.23045` nel log non e la planned qty del nuovo comando market;
- `579.23045` e la vecchia planned qty della leg 1 limit iniziale;
- `519` e la qty fillata della nuova market convertita.

## Implicazione pratica

Se si vuole che il clean log racconti il caso in modo corretto, il campo `planned_qty` mostrato per `ENTRY_OPENED` dopo un `MARKET_ENTRY_NOW` non dovrebbe prendere il valore storico della leg limit originaria.

Dovrebbe invece mostrare uno di questi due concetti:

- la qty teorica ricalcolata della nuova market convert;
- oppure una label esplicita tipo `planned qty from original limit` per evitare ambiguita.
