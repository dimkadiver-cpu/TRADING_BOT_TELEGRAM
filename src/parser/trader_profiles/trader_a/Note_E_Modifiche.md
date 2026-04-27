0) i report cvs conprendono report?

1) quando identifica "CLOSE_FULL | TP_HIT | REPORT_FINAL_RESULT"
    logica: se ha s 

2) caso 1862:    MOVE_STOP_TO_BE | EXIT_BE

Per 1823 i tre intent arrivano perché il testo è molto corto e ambiguo e nel trader_a il
  matching è volutamente largo sui marker стоп/BE.

  In pratica:

  - MOVE_STOP_TO_BE scatta dal ramo stop-management, che nel ruleset considera anche marker
    deboli su стоп e contesto reply su un segnale.
  - SL_HIT scatta dallo stesso blocco di stop, perché il parser usa una semantica ampia per non
    perdere i casi di stop/stop moved/stop hit.
  - EXIT_BE scatta dal ramo BE/exit, che nel ruleset è anch’esso broad e interpreta la chiusura
    in BE come intent separato.

  Il punto decisivo è questo: nel record salvato il path common stava ancora accumulando tutti
  gli intent rilevati, senza applicare il pruning di disambiguazione. Quindi il risultato
  storico resta MOVE_STOP_TO_BE | SL_HIT | EXIT_BE.

  Nel ruleset la preferenza corretta è già definita: EXIT_BE deve prevalere su MOVE_STOP_TO_BE.
  Quindi il motivo del triplo intent non è “tre significati diversi”, ma overlap di regole +
  disambiguazione non applicata nel salvataggio di quel risultato.

  Se rigeneri oggi il common path con la logica agganciata, questo caso dovrebbe ridursi a
  EXIT_BE solo.





- Casi: 110,109,108 hanno "перезаход"



- caso 203: Я вообще по битку переобуюсь. 
Закрываю шорт. Открываю лонг 
 Revers: è un reply al messaggio SIGNAL con un nuovo signal
 logica- se è ha ref, se ha srtuttura signal allora e un signal Revers:
    - chiudi il ref
    - tratta come nuovo signal



- casi 1774,1384,1383,1382,1381,1377,1308
 sono aggiornamento informativi su trade aperti parzialmente prima (classificati come incompleti?) logica:
    se ha un ref e ha struttura signal 
    se segnale aperto modifica tp 




- messaggio non blocati da blacklist:
    - 1919 : Друзья, привет! Админ на связи 👋
    - 1509 : Друзья, #админ на связи 👋


: nel contarro: primary_intent dovrebbe essere quello che promove un tipo verso altro :
    - sempio se abbiamo tp_hit + Report_filanl + close_full (esempio caso 1910)

 Update problemi:

- caso 1875 e altri. ha solo intents ENTRY_FILLED ma vine classificato come Update
- caso 206:  TP_HIT | EXIT_BE (Давайте тут зафиксируем 50%. Не будем дожидаться 1 тейка. Все равно рядом с ним)

- caso 954: CANCEL_PENDING_ORDERS | EXIT_BE ()

- come vengono getite i messaggi con target multipli che hanno i riferimenti a piu target ma con azioni diversi/entita diversi

conratto :
- come vengono getite i messaggi con target multipli che hanno i riferimenti  



Regole: (aggiungere livello di lavidazione ???)

- per classifica re come "Update" - un ref, (ref + signal father) altrimenti info 
- per "Cancel_pending_orders" - ref + signal storico +
- per "Exit_be" - Signal father + move_stop
- Incompatibili 1 allo stesso ref-unico "Cancel_pending_orders" + "Exit_be" 
                2 CLOSE_FULL | EXIT_BE????
                3 CLOSE_FULL | SL_HIT

- Compotibili: CLOSE_FULL | REPORT_FINAL_RESULT
                EXIT_BE | REPORT_FINAL_RESULT
                EXIT_BE | REPORT_FINAL_RESULT