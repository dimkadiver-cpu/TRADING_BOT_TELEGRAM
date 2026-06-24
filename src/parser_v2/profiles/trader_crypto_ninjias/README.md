# Trader Crypto Ninjias Profile

Profilo `parser_v2` per il dataset `parser_test/db/parser_test__trader_crypto_ninjias.sqlite3`.

## Scope

Questo profilo e' costruito sui pattern realmente osservati nel DB e oggi copre soprattutto:

- nuovi segnali `LONG` / `SHORT`
- varianti `RISK ORDER`, `RISK ORDER - SMALL VOL`, `LONG MARKET`, `LONG LIMIT`, `LONG SWING`
- report di esito (`TP_HIT`, `SL_HIT`, `EXIT_BE`, `REPORT_RESULT`)
- alcuni update operativi (`MOVE_STOP_TO_BE`, `CLOSE_FULL`, `CANCEL_PENDING`)
- messaggi di hold o promo come `INFO`

## Strutture Di Segnale Estratte

### 1. Segnale standard con `Entry market` + `Entry limit`

Esempio:

```text
SHORT - $ZEC

- Entry market: 397.93
- Entry limit: 437.82
- SL: 481.35

TP1: 312
TP2: 244.5
TP3: 155.89
TP4: 39.75
```

Output atteso:

- `primary_class=SIGNAL`
- `side=SHORT` o `LONG`
- `symbol=<TICKER>USDT`
- `entries[0]=MARKET`
- `entries[1]=LIMIT`
- `entry_structure=TWO_STEP`
- `stop_loss`
- `take_profits[]`

### 2. Segnale `LONG LIMIT` con due entry limit esplicite

Esempio:

```text
LONG LIMIT - $VELO

- Entry limit 1: 0.003047
- Entry limit 2: 0.002836
- SL: 0.002658

TP1: 0.003420
TP2: 0.003988
TP3: 0.007663
```

Output atteso:

- `primary_class=SIGNAL`
- due entry `LIMIT`
- `entry_structure=TWO_STEP`

### 3. Segnale inline compatto

Esempio:

```text
SPX short headging entry 0.3239 entry limit 0.3410

TP1 0.2926
TP2 0.2608
TP3 0.2252
```

Output atteso:

- `primary_class=SIGNAL`
- `symbol=SPXUSDT`
- `side=SHORT`
- se manca `SL`, il messaggio resta `SIGNAL/PARTIAL`

### 4. Segnale `RISK ORDER` con `Entry:` primaria

Esempio:

```text
LONG - $DYDX - RISK ORDER - SMALL VOL

- Entry: 0.1025
- Entry limit: 0.0980
- SL: 0.0940

TP1: 0.1107
TP2: 0.1201
TP2: 0.1731
```

Output atteso:

- `primary_class=SIGNAL`
- `symbol=DYDXUSDT`
- `side=LONG`
- `Entry:` viene trattata come entry primaria `MARKET` quando nello stesso messaggio esiste una `Entry limit:`
- `Entry limit:` viene trattata come seconda entry
- `entry_structure=TWO_STEP`
- i TP duplicati vengono rinumerati in sequenza quando la ladder continua chiaramente
  quindi `TP1`, `TP2`, `TP2` diventa `TP1`, `TP2`, `TP3`

### 5. Segnale `RISK ORDER` con `TP:` singolo

Esempio:

```text
LONG MARKET - $SEI - RISK ORDER - SMALL VOL

- Entry: 0.2970
- SL: 0.2816
TP: 0.4529
```

Output atteso:

- `primary_class=SIGNAL`
- `entry_structure=ONE_SHOT`
- se l'header contiene `MARKET`, la entry primaria viene marcata `MARKET`
- anche `Entry market(now):` viene trattata come `MARKET`
- `TP:` senza numero viene normalizzato come `TP1`

### 6. Segnale `RISK ORDER` con range su `Entry:`

Esempio:

```text
LONG - $RESOLV - RISK ORDER

- Entry: 0.1511 - 0.1507
- SL: 0.1437
TP: 0.1992
```

Output atteso:

- `primary_class=SIGNAL`
- due entry `LIMIT`
- `entry_structure=TWO_STEP`
- il range viene ordinato per prezzo crescente prima di costruire le entry

## Report Supportati

### 1. TP hit manuale

Esempi:

```text
SHIBA hit TP2 + 2.7R
LTC hit TP1 + 4R
```

Output atteso:

- `primary_class=REPORT`
- `primary_intent=TP_HIT`
- `report.events[0].event_type=TP_HIT`
- `level` valorizzato quando presente

### 2. Full TP

Esempio:

```text
DOGE hit full TP + 6R
```

Output atteso:

- `primary_class=REPORT`
- `primary_intent=TP_HIT`
- evento `TP_HIT`
- anche `report.result.raw_fragment` con il testo completo

### 3. TP hit auto da messaggi Cornix-style

Esempio:

```text
#FIL/USDT Take-Profit target 2
Profit: 7.6005%
```

Output atteso:

- `primary_class=REPORT`
- `primary_intent=TP_HIT`
- `level=2`

### 4. SL hit

Esempi:

```text
THE sweep sl -1R, wait to cover again
DOGS hit sl -1R, wait to cover
```

Output atteso:

- `primary_class=REPORT`
- `primary_intent=SL_HIT`

### 5. Break-even

Esempio:

```text
WLD hit BE
```

Output atteso:

- `primary_class=REPORT`
- `primary_intent=EXIT_BE`

### 6. Report sintetico a `R`

Esempi:

```text
XLM + 3R
HAEDAL + 5R
```

Output atteso:

- `primary_class=REPORT`
- `primary_intent=REPORT_RESULT`
- `report.result.raw_fragment=<testo completo>`

## Update Supportati

### 1. Move stop to entry

Esempio:

```text
SHIBA move sl to entry then wait
```

Output atteso:

- `primary_class=UPDATE`
- `primary_intent=MOVE_STOP_TO_BE`
- action canonica `SET_STOP` con target `ENTRY`

### 2. Close full at entry

Esempi:

```text
DYDX close at entry, wait for new entry
PROVE close order at entry and wait
```

Output atteso:

- `primary_class=UPDATE`
- `primary_intent=CLOSE_FULL`
- action canonica `CLOSE/FULL`

### 3. Cancel entry limit

Esempio:

```text
SOON cancel entry limit and cancel sl then wait
```

Output atteso:

- `primary_class=UPDATE`
- `primary_intent=CANCEL_PENDING`
- action canonica `CANCEL_PENDING`
- `cancel_scope_hint=ALL_PENDING`

## Info Supportati

Vengono degradati a `INFO` messaggi come:

- `keep holding`
- `still hold and wait`
- promo / livestream / Cornix / flash sale / private group

## Non Ancora Coperti

Questi pattern sono volutamente lasciati fuori perche' non hanno ancora una traduzione canonica affidabile nel contratto attuale:

- `cancel sl`
- `set TP4`
- `set TP4 TP5`
- varianti miste come `cancel sl then wait` senza `cancel entry limit`

## Validazione Rapida

Test mirati del profilo:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest src\parser_v2\tests\test_trader_crypto_ninjias_profile.py -q
```

Replay sul DB reale:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe parser_test\scripts\replay_parser_v2.py `
  --db-path parser_test\db\parser_test__trader_crypto_ninjias.sqlite3 `
  --trader-filter trader_crypto_ninjias `
  --parser-profile auto `
  --force-reparse
```
