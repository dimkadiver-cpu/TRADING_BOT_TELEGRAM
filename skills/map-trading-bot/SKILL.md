---
name: map-trading-bot
description: Usa questa skill per mappare il progetto trading bot, identificare moduli chiave, entry points, pipeline dati, file obsoleti e punti di intervento sicuri.
---

# Obiettivo
Capire rapidamente la struttura del trading bot senza modificare codice.

# Quando usarla
- quando il codebase non è ancora chiaro
- prima di introdurre una nuova feature
- prima di toccare parser, lifecycle o DB
- quando serve trovare file obsoleti o duplicati

# Workflow
1. Individua entry points reali del progetto
2. Mappa il flusso dati da Telegram a DB e parser
3. Elenca moduli principali e responsabilità
4. Segnala file obsoleti, duplicati o rischiosi
5. Evidenzia dove intervenire con il minimo rischio
6. Restituisci una sintesi operativa

# Output richiesto
Restituisci sempre:
- mappa cartelle e moduli
- entry points
- pipeline dati
- funzioni critiche
- file sospetti o legacy
- raccomandazione su dove implementare il cambiamento richiesto

# Regole
- non modificare file
- non proporre riscrittura totale se non strettamente necessaria
- sii concreto sui nomi file
- segnala debiti tecnici senza drammatizzare