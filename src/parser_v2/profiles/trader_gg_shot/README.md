# Trader GG Shot Profile

Profilo `parser_v2` per il dataset `parser_test/db/parser_test__trader_gg_shot.sqlite3`.

## Scope

Questo profilo e' costruito sui pattern realmente osservati nel DB e oggi copre soprattutto:

- nuovi segnali `📩 ... Entry Zone`
- report strutturati `📬 Report on ...`
- reply report compatti `#SYM first/two/three/all targets done`
- un update operativo `Closed at the entrance #SYM`
- market analysis, daily report e promo come `INFO`

## Strutture Di Segnale Estratte

### 1. Segnale standard con `Entry Zone`

Esempio:

```text
📩 #SEIUSDT 30m | Mid-Term
📉 Short Entry Zone: 0.05272-0.05547

⏳ Signal Details:
Target 1: 0.05124
Target 2: 0.04977
Target 3: 0.04829
Target 4: 0.04386

🔺 Stop-Loss: 0.05702
```

Output atteso:

- `primary_class=SIGNAL`
- `side=SHORT` o `LONG`
- `symbol=<TICKER>USDT`
- due entry `LIMIT`
- `entry_structure=TWO_STEP`
- `stop_loss`
- `take_profits[]`

## Report Supportati

### 1. Report strutturato con target raggiunti

Esempio:

```text
📬 Report on #TONUSDT 30m | Mid-Term
📈 Long was opened at - 1.6877

⛳️ Two targets done: +18.2% (x10lev)
```

Output atteso:

- `primary_class=REPORT`
- `primary_intent=TP_HIT`
- `level=2` quando il numero di target e' esplicito

### 2. Report strutturato con stop loss

Esempio:

```text
📬 Report on #BNBUSDT 30m | Mid-Term
❌ Reaching Stop-Loss: -6% (x10lev)
```

Output atteso:

- `primary_class=REPORT`
- `primary_intent=SL_HIT`

### 3. Reply report compatto

Esempi:

```text
#CAKE first target done ✅
#CAKE two targets done ✅
#TON bam, all targets done 💥
```

Output atteso:

- `primary_class=REPORT`
- `primary_intent=TP_HIT`
- `level=1|2|3` quando presente
- `all targets done` resta `TP_HIT` senza level obbligatorio

## Info Supportati

Vengono degradati a `INFO` messaggi come:

- `BTC - Market Analysis`
- `Daily report`
- promo / bot / mini app / website update

## Update Supportati

### 1. Close full at entry

Esempio:

```text
Closed at the entrance #NEO
```

Output atteso:

- `primary_class=UPDATE`
- `primary_intent=CLOSE_FULL`
- action canonica `CLOSE/FULL`
- targeting via `reply_to_message_id` del messaggio parent

## Non Ancora Coperti

Questi pattern sono volutamente lasciati fuori perche' nel contratto attuale non sono ancora modellati in modo affidabile:

- report ultra-compatti tipo `#BTC 66600 ✅ +180%`
- post multi-symbol tipo `#AR #CAKE #FARTCOIN all targets done`
- update operativi impliciti basati solo sul reply chain
- `cancel pending` standalone
- `move stop to BE` standalone
- chiusure parziali standalone

## Validazione Rapida

Test mirati del profilo:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest src\parser_v2\tests\test_trader_gg_shot_profile.py -q
```

Replay sul DB reale:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe parser_test\scripts\replay_parser_v2.py `
  --db-path parser_test\db\parser_test__trader_gg_shot.sqlite3 `
  --trader-filter trader_gg_shot `
  --parser-profile auto `
  --force-reparse
```
