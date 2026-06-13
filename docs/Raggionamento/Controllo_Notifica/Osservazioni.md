1.  Spec: C:\TeleSignalBot\docs\superpowers\specs\2026-06-06-log-templating-design.md
 differenze in ENTRY OPENED ricevute e quello in esempio (C:\TeleSignalBot\docs\Raggionamento\Controllo_Notifica\Template_clean_log\Clean_log_entry_entry_update.md):


2. Verificacare se tutti errori dericati da processo di cicly life  non notificati
2.1 in caso di un errore dopo avera acettato un segnale/update erroe vien enotificato in techlog, mentre quello gi ainserito dve veesserre modificato, esempi  


✅ #24 — UPDATE DONE                // di fatto non e stato eseguito per via del errore
- - - - - - - - - - - - - - -
AVAX/USDT — 📈 LONG
https://t.me/c/4240829081/358
- - - - - - - - - - - - - - -
Operation:
▪️ MOVE_SL_TO_BE
Changed:
▪️ SL: 6.48 → 6.615 *
- - - - - - - - - - - - - - -
* BE
- - - - - - - - - - - - - - -
Source: trader_update
https://t.me/c/3722628653/7556

in tech:

 [ERROR] Gateway: entry_all_failed
────────────────
Tutti i comandi PLACE_ENTRY falliti. Catena cancellata.

Context:
chain_id: 23
symbol: AVAX/USDT
side: LONG
reason: deferred_market_zero_risk_distance

Action: intervento manuale richiesto
────────────────
Source: execution_gateway