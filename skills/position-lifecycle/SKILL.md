---
name: position-lifecycle
description: Usa questa skill per definire o aggiornare il ciclo di vita delle posizioni, con stati, transizioni, validazioni ed edge case.
---

# Obiettivo
Mantenere coerente e verificabile lo stato posizione nel tempo.

# Quando usarla
- nuove regole di update
- gestione partial TP o full close
- move stop o break-even
- cancellazione ordini pendenti
- dubbi sul significato operativo di un evento canonico

# Workflow
1. Identifica lo stato attuale della posizione
2. Analizza l'evento canonico in ingresso
3. Verifica se l'evento è applicabile
4. Definisci la transizione consentita
5. Elenca campi da salvare
6. Segnala edge case e conflitti
7. Suggerisci test minimi

# Stati tipici da considerare
- signal_detected
- pending_order
- partially_filled
- active_position
- partially_closed
- fully_closed
- canceled
- invalidated

# Output richiesto
Restituisci sempre:
- stati ammessi
- transizioni ammesse
- vincoli di validazione
- effetti sul DB o audit trail
- edge case principali
- test necessari

# Regole
- nessuna transizione implicita
- cancellare pending non equivale a chiudere una posizione
- chiusure parziali e totali restano distinte
- ogni mutazione deve essere spiegabile da un evento preciso