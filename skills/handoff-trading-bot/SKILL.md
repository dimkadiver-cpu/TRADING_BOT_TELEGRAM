---
name: handoff-trading-bot
description: Usa questa skill per aggiornare README, changelog e note operative dopo modifiche al trading bot.
---

# Obiettivo
Lasciare una traccia operativa chiara dopo ogni task importante.

# Quando usarla
- dopo aggiunta di un parser trader-specific
- dopo modifica a lifecycle
- dopo cambi schema o DB
- dopo fix significativo
- prima di chiudere una sessione di lavoro

# Workflow
1. Riassumi cosa è stato fatto
2. Elenca file toccati
3. Spiega il comportamento nuovo o corretto
4. Annota limiti aperti
5. Indica test eseguiti o mancanti
6. Prepara prompt per la prossima sessione

# Output richiesto
Restituisci sempre:
- changelog sintetico
- file modificati
- stato attuale del sistema
- rischi o TODO aperti
- prossimo prompt consigliato

# Regole
- scrivi in modo operativo
- evita romanzi inutili
- evidenzia ciò che potrebbe rompersi dopo
- prepara continuità, non poesia tecnica