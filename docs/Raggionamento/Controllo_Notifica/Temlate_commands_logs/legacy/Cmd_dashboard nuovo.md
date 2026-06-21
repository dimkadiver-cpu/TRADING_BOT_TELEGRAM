Modifiche:

- in prima riga riasunto  

- Elenco dei elementi in dash boarsd deve essere compatto 3/4 righi per item

- "/trade n." port la storia deltaliglia del trade dalla sual recezione fina alla chiusura cancelazione:



⚡ Active — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
 Total: 10            Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
#5 · BTC/USDT · LONG · PARTIALLY_CLOSED
uPnL:  +34.20 USDT  rPnL:  +14.20 USDT
/trade 5 · /cancel 5 · /close 5
- - - - - - - - - - - - - - - - - - - -
#6  BTC/USDT  LONG  PARTIALLY_CLOSED
uPnL:  +34.20 USDT  rPnL:  +14.20 USDT
/trade 6 · /cancel 6 · /close 6
- - - - - - - - - - - - - - - - - - - -
#7  SOL/USDT  LONG  WAITING_ENTRY
uPnL:                rPnL:               // vuoto o assente
/trade 7 · /cancel 7 · /close 7



✅ Closed — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
 Total: 10            Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
#22 · BTC/USDT · LONG  · STOP_LOSS
Net PnL: -3.20 USDT · ⏱ 2h 34m
Details: /trade 22 
- - - - - - - - - - - - - - - - - - - -
#18 · SOL/USDT · LONG  · TP_COMPLETE
Net PnL: +34.50 USDT · ⏱ 4h 45m
Details: /trade 22 
- - - - - - - - - - - - - - - - - - - -
### Cancelled without fill

```text
#24  ETH/USDT · LONG · CANCEL_PENDING
PnL: No fill
Details: /trade 22 



🚫 Blocked — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
Total: 1           Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
#7 · ETH/USDT · LONG  
Blocked: 14 Jun 11:52 · Reason: missing_sl
Details: /trade 7



/trade n

#5 · BTC/USDT · LONG · PARTIALLY_CLOSED 
- - - - - - - - - - - - - - - - - - - -
Trader: trader_devos_crypto
Exchange Account: demo_2
Updated: 14:32:05                           // ultimo aggiornamento ricevuto (snapshot_position se ancora aperto o evento rigestrato tipo chiusura )
- - - - - - - - - - - - - - - - - - - -
Entry: 63,500 ✓ · 63,200 ✗ · 62,800 ✗
TP:    64,000 ✓ · 65,200 · 66,500
SL:    62,000 · BE: No
uPnL:  +34.20 USDT  rPnL:  +14.20 USDT
- - - - - - - - - - - - - - - - - - - -
 Actions:  /cancel 5 · /close 5
- - - - - - - - - - - - - - - - - - - -
Events:
• SIGNAL ACCEPTED · 14 Jun 09:10:00 
   Source: Signal                        // link a signal acepted
• ENTRY OPENED · 14 Jun 09:10:00
  Source: exchange                        // link a log ENTRY OPENED
• TP1 FILLED · 14 Jun 09:10:01 
  Source: exchange
• UPDATE DONE ·14 Jun 09:10:02 ·
   Type: CANCEL_PENDING
  Source: operation_rules                 // link a log UPDATE DONE (silnolo e anche a multiupdate se fa parte)


#5 · BTC/USDT · LONG · WAITING_ENTRY
- - - - - - - - - - - - - - - - - - - -
Trader: trader_devos_crypto
Exchange Account: demo_2
Updated: 14:32:05                           // ultimo aggiornamento ricevuto (snapshot_position se ancora aperto o evento rigestrato tipo chiusura )
- - - - - - - - - - - - - - - - - - - -
Entry: 63,500  · 63,200  · 62,800 
TP:    64,000  · 65,200 · 66,500
SL:    62,000 · BE: No
uPnL:  00.00 USDT  rPnL:  00.00 USDT           // quaesta sezione sparisce  quando la positione e chiusa 
- - - - - - - - - - - - - - - - - - - -
 Actions:  /cancel 5 · /close 5               // quaesta sezione sparisce  quando la positione e chiusa 
- - - - - - - - - - - - - - - - - - - -
Events:
• SIGNAL ACCEPTED · 14 Jun 09:10:00 
   Source: Signal                        // link a signal acepted





#5 · BTC/USDT · LONG · POSITION CLOSED 
- - - - - - - - - - - - - - - - - - - -
Trader: trader_devos_crypto
Exchange Account: demo_2
- - - - - - - - - - - - - - - - - - - -
Updated: 14:32:05                           // ultimo aggiornamento ricevuto (snapshot_position se ancora aperto o evento rigestrato tipo chiusura )
- - - - - - - - - - - - - - - - - - - -
Entry: 63,500 ✓ · 63,200 ✗ · 62,800 ✗
TP:    64,000 ✓ · 65,200 ✓  
SL:    62,000 · BE: No                 
- - - - - - - - - - - - - - - - - - - -
Final Result:
ROI net: +3.67% · RoR: +9.12% · R: +0.22R
PnL net: +44.17 USDT · PnL gross: +45.20 USDT
Fees: -2.06 USDT  · Funding: +0.03 USDT
- - - - - - - - - - - - - - - - - - - -
Events:
• SIGNAL ACCEPTED · 14 Jun 09:10:00 
   Source: Signal                        // link a signal acepted
• ENTRY OPENED · 14 Jun 09:10:00
  Source: exchange                        // link a log ENTRY OPENED
• TP1 FILLED · 14 Jun 09:10:01 
  Source: exchange
• UPDATE DONE ·14 Jun 09:10:02 ·
   Type: CANCEL_PENDING
   Source: operation_rules                 // link a log UPDATE DONE (silnolo e anche a multiupdate se fa parte)
• POSITION CLOSED ·14 Jun 09:10:02 ·
  Reason: FINAL TP FILLED
  Source: exchange                       // link


