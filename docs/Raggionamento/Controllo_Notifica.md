# Controllo Notifiche — come si comportano, in parole semplici

Questo documento spiega, caso per caso, **come il bot decide quando e in che ordine
inviare le notifiche** su Telegram. Niente codice: solo il comportamento.

---

## L'idea di base

Ogni notifica (signal, apertura, update, take profit, stop, chiusura…) viene prima
**scritta in una coda** (l'"outbox") nel database. Un processo ("dispatcher") guarda la
coda ogni paio di secondi e **spedisce** i messaggi a Telegram.

Due regole governano tutto:

1. **Ordine** — i messaggi di una stessa operazione (chain) escono nell'ordine in cui sono
   stati creati.
2. **Dipendenza dal "root"** — il primo messaggio di un'operazione è il **segnale**
   (`SIGNAL_ACCEPTED`). Gli altri messaggi (apertura, TP, chiusura…) vogliono "agganciarsi"
   a quel segnale con un link/risposta. Quindi **aspettano** che il segnale sia stato
   inviato, per poter mettere il link.

---

## Il "root" e perché si aspetta

Il **root** = il messaggio del **segnale accettato** di quella operazione.

Quando arriva, ad esempio, l'**apertura posizione**, il bot vuole che quel messaggio sia
una risposta al segnale (così su Telegram vedi la conversazione collegata). Per farlo gli
serve l'ID del messaggio del segnale.

- Se il segnale è **già stato inviato** → il bot ha l'ID, mette il link, invia subito.
- Se il segnale **non è ancora stato inviato** → il bot **aspetta** un po' prima di inviare
  l'apertura.

---

## La regola dell'attesa (con scadenza)

Qui sta la differenza importante rispetto a prima.

L'attesa **non è infinita**. C'è una **scadenza** (di default **45 secondi**):

- Finché non scade, ogni pochi secondi il bot ricontrolla se il segnale è arrivato.
- Se il segnale arriva entro la scadenza → invia l'evento **con il link**. ✅
- Se la scadenza **passa** e il segnale ancora non c'è → il bot **invia comunque l'evento,
  ma senza link**, e scrive un **avviso (WARNING) nel TECH_LOG** per segnalare che mancava
  il segnale di quella operazione. ✅

In questo modo **non si perde mai un evento** (apertura, TP, chiusura li vedi sempre) e il
bot **non resta mai bloccato** ad aspettare all'infinito.

> **Perché prima era lento:** la vecchia versione (commit chain-32) faceva aspettare
> **senza scadenza**. Se il segnale non arrivava mai (es. timeout di rete), l'evento veniva
> rimesso in coda ogni 2 secondi per sempre, intasando il database e rallentando sia le
> notifiche sia le risposte ai comandi. La scadenza elimina questo problema.

---

## L'ordine dei messaggi

Tutti gli eventi di una stessa operazione escono **nell'ordine in cui sono stati creati**.
Esempio tipico di un'operazione:

```
1. SEGNALE ACCETTATO        (il root)
2. APERTURA POSIZIONE       (risponde al root)
3. UPDATE (modifica piano)
4. TAKE PROFIT 1
5. STOP SPOSTATO a pareggio
6. TAKE PROFIT 2
7. CHIUSURA POSIZIONE
```

Garanzie:
- La **chiusura non può uscire prima dell'apertura** della stessa operazione.
- Se più eventi della stessa operazione sono in attesa del root, **quando il root arriva
  escono tutti in ordine**, dal più vecchio al più recente.
- Operazioni **diverse** non si bloccano a vicenda: se l'operazione A aspetta il suo root,
  l'operazione B continua a inviare normalmente.

---

## Caso per caso

### 1. Segnale accettato (root)
È il primo messaggio. Non aspetta nessuno: viene inviato appena possibile. Una volta
inviato, "sblocca" tutti gli eventi successivi di quella operazione.

### 2. Apertura posizione (open)
Aspetta il root. Se il root c'è → invia con link. Se manca e scade la finestra → invia
senza link + avviso TECH_LOG.
Questo è il famoso **caso chain-32**: l'apertura era arrivata *prima* del segnale.

### 3. Update con UNA modifica
Un singolo cambiamento (es. stop spostato). Aspetta il root come gli altri, poi esce in
ordine.

### 4. Update con PIÙ azioni insieme (update multiplo di campi)
Se in un colpo solo cambiano più cose (es. cancella entry + sposta stop), **non escono N
messaggi**: il bot li **unisce in un unico messaggio** "UPDATE" che elenca tutte le
modifiche. Questa unione avviene **a monte**, prima della coda, e **non cambia** con le
nuove regole d'ordine.

### 5. Take Profit (uno o più)
Ogni TP è un evento. Più TP nel tempo = più messaggi, ciascuno in ordine. Aspettano il
root come gli altri.

### 6. Stop spostato (a pareggio o altrove)
Evento singolo, stessa regola: ordine + dipendenza dal root.

### 7. Chiusura posizione (close)
È un evento ad **alta importanza**. Esce comunque **dopo** l'apertura della sua operazione
(l'ordine è rispettato). Dopo l'invio della chiusura, il bot controlla se va inviato un
**riepilogo multiplo** (vedi sotto).

### 8. Riepilogo multi-operazione ("multiplo" / MULTI_CHAIN_SUMMARY)
Quando un segnale ha generato **più operazioni** e queste si chiudono, il bot manda un
**riepilogo unico** che le raggruppa. Questo riepilogo **aspetta che tutte** le operazioni
del gruppo siano state chiuse e inviate, con un piccolo ritardo (~3 secondi) per
raccoglierle tutte.
Questo meccanismo **resta com'è** (non è stato cambiato in questa revisione).

---

## Cosa NON è cambiato

- **L'unione degli update multipli** in un solo messaggio: identica a prima.
- **Il riepilogo multi-operazione**: identico a prima.
- **Il contenuto e il tipo dei messaggi**: identici. Cambia solo *quando* e *in che ordine*
  vengono spediti, e il fatto che ora c'è una **scadenza** all'attesa.

---

## Riassunto in una frase

> Ogni evento di un'operazione esce **in ordine** e **agganciato al suo segnale**; se il
> segnale tarda oltre **45 secondi**, l'evento esce **comunque** (senza aggancio) e viene
> segnalato nel TECH_LOG — così non si perde niente e niente resta bloccato.
