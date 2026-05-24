- Cosa succede se un ordine non parte per un errore? poi metto un ordine con stesso symbolo?

- Aggiornare file Readme, CLAUDE.md

- pulizia finale da vecchi file/non in uso ecc. (fatta in parte)

- ottimizare il worker di esecuizione dei comandi su cicle life

Domande aperte da verificare:

- Verificare come vengono gestiti i ordini su exchange: tre modalita

- Velocizare i passaggi? sync, ecc

- manpulazioni manuale su exchange, vengono rivelati e  rigestrati su DB?

- Regolazione di BE (punti extra come vengano fatte? modalita Fee, per co da agiungere?)

---

Domande risolte (2026-05-23):

- [x] in ops execution_commands dopo aver fatto "sent" fa il sync per vedere se operazione andato a buon fine? e poi segna done?
  → I comandi fire-and-forget (CANCEL, SYNC, MOVE_STOP, SET_TPSL) non creano ordini pollabili.
     Ora vengono marcati DONE immediatamente dopo mark_sent (_FIRE_AND_FORGET in gateway.py).
     I comandi con ordine reale (PLACE_ENTRY, PLACE_STOP, PLACE_TP) vengono marcati DONE
     dal sync worker quando trova il fill/cancel sull'exchange.

→ Vedi stato completo: docs/debugging/stato_runtime_v2.md
