# TeleSignalBot Documentation

Questa cartella contiene la documentazione autorevole del progetto.

## Ordine di lettura consigliato

1. `MASTER_PLAN.md`
   Documento guida. Spiega obiettivo, moduli, flusso completo e ordine di sviluppo.

2. `SYSTEM_ARCHITECTURE.md`
   Vista tecnica ad alto livello del sistema e dei collegamenti tra blocchi.

3. `PARSER_FLOW.md`
   Spiega come il parser lavora a fasi, senza mescolare dettagli di exchange o rischio.

4. `DB_SCHEMA.md`
   Riassume le tabelle principali e il ruolo logico dei dati persistiti.

5. `TRADE_STATE_MACHINE.md`
   Definisce il ciclo di vita di un trade gestito dal sistema.

6. `RISK_ENGINE.md`
   Descrive la logica di sizing e i blocchi di rischio.

7. `EXCHANGE_PRECISION_ENGINE.md`
   Spiega come normalizzare prezzi e quantità prima dell'invio ordini.

8. `CONFIG_SCHEMA.md`
   Descrive i file di configurazione e cosa ci si aspetta dentro.

9. `BOT_COMMANDS.md`
   Elenca i comandi del bot Telegram e il loro ruolo operativo.

10. `TASKS.md`
    Roadmap pratica di implementazione.

11. `CODEX_BOOTSTRAP.md`
    Regole operative per sviluppare con Codex senza creare caos.

## Nota

I file dentro `docs/archive/` non sono più documenti guida. Restano solo come materiale storico o bozze precedenti.
