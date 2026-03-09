---
name: build-parser-profile
description: Usa questa skill quando devi costruire o aggiornare un parser per un trader specifico, con pattern, sinonimi, estrazione entità, linking e output canonico.
---

# Obiettivo
Produrre parser trader-specific robusti senza rompere l'output canonico del sistema.

# Quando usarla
- nuovo trader profile
- aggiornamento vocabolario di un trader
- fix di classificazione messaggi
- aggiunta di campi estratti
- miglioramento linking root/update

# Workflow
1. Analizza gli esempi del trader
2. Definisci vocabolario, sinonimi e pattern
3. Distingui classificazione da estrazione entità
4. Definisci regole di linking al segnale originario
5. Normalizza tutto nell'output canonico
6. Elenca casi ambigui
7. Prepara casi test minimi

# Entità da cercare
Quando presenti, prova a estrarre:
- symbol o instrument
- side
- market_type
- entries
- stop_loss
- take_profits
- leverage
- note operative come cancel, move stop, close

# Output richiesto
Restituisci sempre:
- classe evento prevista
- pattern chiave trovati
- campi estraibili
- strategia di linking
- output canonico atteso
- edge case
- test minimi

# Regole
- preferisci robustezza alla creatività
- se un campo non è affidabile, dichiaralo ambiguo
- separa detection, extraction, normalization e linking
- non mescolare logica trader-specific con utility condivise