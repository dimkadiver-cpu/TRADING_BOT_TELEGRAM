## `/trade #n` — position closed

```text
#5 · BTC/USDT · LONG · POSITION CLOSED
- - - - - - - - - - - - - - - - - - - -
Trader: trader_devos_crypto
Exchange Account: demo_2
Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
Entry: 63,500 ✓ · 63,200 ✗ · 62,800 ✗      // descizione 1 sezione dedicata al setup inizia
TP:    64,000 ✓ · 65,200 ✓
SL:    62,000 · BE: No
- - - - - - - - - - - - - - - - - - - -
Final Result:                                 // descizione 2 
ROI net: +3.67% · RoR: +9.12% · R: +0.22R
PnL net: +44.17 USDT · PnL gross: +45.20 USDT
Fees: -2.06 USDT · Funding: +0.03 USDT
- - - - - - - - - - - - - - - - - - - -
Events:                                     // descizione 3 
• SIGNAL ACCEPTED · 14 Jun 09:10:00
  Source: Signal -> clean_log

• ENTRY OPENED · 14 Jun 09:10:00
  Source: exchange -> clean_log

• TP1 FILLED · 14 Jun 09:10:01
  Source: exchange -> clean_log

• UPDATE DONE · 14 Jun 09:10:02
  Type: CANCEL_PENDING
  Source: operation_rules -> clean_log

• POSITION CLOSED · 14 Jun 09:10:02
  Reason: FINAL TP FILLED
  Source: exchange -> clean_log

Descizione 1:  sezione dedicata al setup inizia riassunto dei dati e avenimenti mediante ✗ e ✓ ( ✗ cancellato, ✓ filato/colpito)
 se abbimo 
Descizione 2: sezione dedicata al risultato finale, visisbile  solo se la posione e chiusa


Descizione 3: qui vego registrati solo eventi principali per qule è previsto il clean/tech log, altri eventi interni non segnalare, meglio definire glia la list?
> clean_log dovrebbe essere un meta link che porta a Clean/tech log dedicato