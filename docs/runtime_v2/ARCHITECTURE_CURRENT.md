# Runtime V2 — Current Architecture Map

> Stato del documento: mappa operativa corrente del runtime V2.
>
> Scopo: descrivere la successione dei livelli, i contratti tra componenti, il flusso dati, gli eventi, i comandi e i punti critici da tenere sotto controllo durante modifiche future.

---

## 1. Obiettivo del runtime V2

`runtime_v2` è il core operativo del bot.

Non deve essere letto come un singolo modulo,