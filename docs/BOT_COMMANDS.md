# Telegram Bot Commands

## Scopo

Questi comandi servono per controllo operativo manuale del sistema.

## Comandi

### `/plan`
Mostra il piano trade attuale prima dell'esecuzione o durante la gestione.

### `/confirm`
Conferma il piano e autorizza l'esecuzione quando la modalità del trader richiede approvazione manuale.

### `/cancel`
Annulla il trade o il piano corrente se non è già entrato in una fase irreversibile.

### `/status`
Mostra lo stato attuale del trade o della posizione.

### `/ignore`
Marca il segnale come ignorato e impedisce che venga eseguito.

### `/queue`
Mantiene il trade in coda finché la situazione del simbolo o del portafoglio non lo permette.

### `/override_close_open`
Forza la chiusura della posizione attuale e l'apertura del nuovo piano quando il sistema è configurato per consentirlo.

## Nota operativa

La validità finale di un comando dipende sempre dallo stato attuale del trade e dalle regole di sicurezza del sistema.
