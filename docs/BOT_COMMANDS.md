# Telegram Bot Commands

## Scopo

Questi comandi descrivono il controllo operativo manuale previsto per il sistema.

## Stato implementazione

Questo documento e una specifica target.
Al momento `src/telegram/bot.py` e ancora un placeholder e questi comandi non sono disponibili nel runtime corrente.

## Comandi previsti

### `/plan`
Mostra il piano trade attuale prima dell'esecuzione o durante la gestione.

### `/confirm`
Conferma il piano e autorizza l'esecuzione quando la modalita del trader richiede approvazione manuale.

### `/cancel`
Annulla il trade o il piano corrente se non e gia entrato in una fase irreversibile.

### `/status`
Mostra lo stato attuale del trade o della posizione.

### `/ignore`
Marca il segnale come ignorato e impedisce che venga eseguito.

### `/queue`
Mantiene il trade in coda finche la situazione del simbolo o del portafoglio non lo permette.

### `/override_close_open`
Forza la chiusura della posizione attuale e l'apertura del nuovo piano quando il sistema e configurato per consentirlo.

## Nota operativa

Quando questi comandi saranno implementati, la loro validita finale dipendera sempre dallo stato attuale del trade e dalle regole di sicurezza del sistema.
