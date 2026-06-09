1.  Spec: C:\TeleSignalBot\docs\superpowers\specs\2026-06-06-log-templating-design.md
 differenze in ENTRY OPENED ricevute e quello in esempio (C:\TeleSignalBot\docs\Raggionamento\Controllo_Notifica\Template_clean_log\Clean_log_entry_entry_update.md):

📊 #7 — ENTRY OPENED
- - - - - - - - - - - - - - -
BEAT/USDT — 📈 LONG
https://t.me/c/3897279123/609
- - - - - - - - - - - - - - -
Filled:
Entry_1: 4.3948 Market
Qty: 825
Value: 3625.71 USDT
Fee rate: 0.110%
Fee: 3.99 USDT
- - - - - - - - - - - - - - -
Position:
Avg entry: 4.3948
Total qty: 825
Total value: 3625.71 USDT
Total fees: 3.99 USDT
Risk                  // manca
Pending: none
- - - - - - - - - - - - - - -
Source: exchange


1.1

📊 #4 — ENTRY OPENED
- - - - - - - - - - - - - - - - - - - - -
XAU/USDT — 📉 SHORT
https://t.me/c/3897279123/603
- - - - - - - - - - - - - - - - - - - - -
Filled:
Entry_1: 4,333.54 Limit
Qty: 11.299 (planned: 11.299435) // "11.299435" arotondare a 3 ???
Value: 48964.67 USDT
Fee rate: 0.028%
Fee: 13.71 USDT
Partial: 100%
- - - - - - - - - - - - - - - - - - - - -
Position:
Avg entry: 4,333.54
Total qty: 11.299
Total value: 48964.67 USDT
Total fees: 13.71 USDT
Filled: 100%                 /
Pending: none
- - - - - - - - - - - - - - - - - - - - -
Changed:
SL qty: 11.299435 → 11.299 (adj. to fill) // motivo??? arodonamento lo risolve???
- - - - - - - - - - - - - - - - - - - - -
Source: exchange


2. verificare la Classificazione di reason in  POSITION CLOSED:
  - Close reason: BREAKEVEN_AFTER_TP: solo se e stato imposytato BE + Source: exchange ()
  - Close reason: MANUAL_CLOSE: Source: manual_command (da comado via /xxxx) 
  - Close reason: TRADER_COMMAND Source: trader_update


3. 📊 #9 — ENTRY OPENED
- - - - - - - - - - - - - - - - - - - -
SOL/USDT — 📈 LONG
https://t.me/c/4240829081/252
- - - - - - - - - - - - - - - - - - - -
Filled:
Entry_1: 64.75 Limit
Qty: 34.7 (planned: 34.782609)
Value: 2246.82 USDT
Fee rate: 0.020%
Fee: 0.45 USDT
Partial: 99.80%
- - - - - - - - - - - - - - - - - - - -
Position:
Avg entry: 64.75
Total qty: 34.7
Total value: 2246.82 USDT
Total fees: 0.45 USDT
Filled: 99.80%              // eliminare
Pending: none
- - - - - - - - - - - - - - - - - - - -
Changed:
SL qty: 34.782609 → 34.7 (adj. to fill)
- - - - - - - - - - - - - - - - - - - -
Source: exchange


4. ✅ #11 — UPDATE DONE          // ricevyto update, dice che ha fatto ma Note: manca regola di esclusione nel parser, manca il controllo di modifica entry (è gia entrato ma ha riconsciuto di intrere al market/ modificare entry)
- - - - - - - - - - - - - - -
HYPE/USDT — 📈 LONG
https://t.me/c/4240829081/263
- - - - - - - - - - - - - - -
Operation:
▪️ MODIFY_ENTRIES
- - - - - - - - - - - - - - -
Source: trader_update
https://t.me/c/3722628653/6863
