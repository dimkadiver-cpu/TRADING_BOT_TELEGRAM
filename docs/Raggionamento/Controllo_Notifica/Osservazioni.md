1.  Spec: C:\TeleSignalBot\docs\superpowers\specs\2026-06-06-log-templating-design.md
 differenze in ENTRY OPENED ricevute e quello in esempio (C:\TeleSignalBot\docs\Raggionamento\Controllo_Notifica\Template_clean_log\Clean_log_entry_entry_update.md):


📊 #4 — ENTRY OPENED
- - - - - - - - - - - - - - - - - - - - -
XAU/USDT — 📉 SHORT
https://t.me/c/3897279123/603
- - - - - - - - - - - - - - - - - - - - -
Filled:
Entry_1: 4,333.54 Limit           // se abbiamo unica entry non numerarla Entry:
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
Filled: 100%                 // si riferisce sempre alla leg? o a tutta posione pianificata? 
Risk // manca
Pending: none
- - - - - - - - - - - - - - - - - - - - -
Changed:
SL qty: 11.299435 → 11.299 (adj. to fill) // motivo??? arodonamento lo risolve???
- - - - - - - - - - - - - - - - - - - - -
Source: exchange


2. verificare la Classificazione di reason in  POSITION CLOSED:
  - Close reason: BREAKEVEN_AFTER_TP: solo se e stato imposytato BE + Source: exchange (ho ricevutro caso in cui classificato come BE ma era TRADER_COMMAND )
  - Close reason: MANUAL_CLOSE: Source: manual_command (da comado via /xxxx o riconciliazione) 
  - Close reason: TRADER_COMMAND Source: trader_update




