Osservazioni:


- estendere "/cancel" "/close" "/trade n." ache per i tipic specifici

- in trade_detail, quando clicco "/cancel_110"  esce "Trade#110 is OPEN — not cancellable (must be WAITING_ENTRY)." in Entry: 0.7173 ✓ · 0.7189 ✓ · 0.737309 · 0.755717. Sarebbe icoretto, dovrebee controllare se cisono entry pending e non lo stato

- Events:

#94 · HYPE/USDT · LONG · POSITION CLOSED
- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
Trader: trader_devos_crypto
Exchange Account: demo_2
Updated: 17:19:03
- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
Entry: 68.593 ✓ · 68.222 ✓ · 66.53357 ✓ · 64.84515 ✗
TP:    68.935965 ✓ · 69.27893 ✓ · 69.96486 ✗ · 70.65079 ✗ · 71.33672 ✗ · 72.02265 ✗ · 72.70858 ✗ · 73.39451 ✗
SL:    — · BE: 67.794101
- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
Final Result:
ROI net: n/a  · RoR: n/a  · R: n/a                   // mancano i dati 
PnL net: +2.57 USDT  · PnL gross: +3.12 USDT
Fees: -0.55 USDT  · Funding: +0.00 USDT
- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
Events:
• SIGNAL ACCEPTED · 22 Jun 01:25:45
  Source: Signal → [clean_log](https://t.me/c/2535735230/147810)

• ENTRY OPENED · 22 Jun 01:26:42
  Source: exchange          // manca  "clean_log" con link

• TP1 FILLED · 22 Jun 13:26:22        // manca  "clean_log" con link
  Source: exchange

• POSITION CANCELLED · 22 Jun 13:26:56      // sarebbe incoretto dobrebbe essee "update_done" con type
  Reason: auto_cancel_averaging             
  Source: exchange

• TP2 FILLED · 22 Jun 13:30:22
  Source: exchange                   // manca  "clean_log" con link

• SL MOVED TO BE · 22 Jun 13:30:23    // sarebbe incoretto dobrebbe essee "update_done" con type
  Source: operation_rules             // manca  "clean_log" con link

• SL HIT · 22 Jun 15:49:01            //  sarebbe incoretto dobrebbe essee "Position_cosed" con reason: BREAKEVEN_AFTER_TP
  Source: exchange                    // manca  "clean_log" con link