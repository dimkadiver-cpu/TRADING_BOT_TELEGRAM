---
name: qa-parser-regression
description: Usa questa skill per controllare regressioni, edge case, copertura test e qualità del diff nel trading bot.
---

# Obiettivo
Trovare problemi prima di commit, merge o rilascio interno.

# Quando usarla
- dopo modifiche a parser
- dopo modifiche a linking
- dopo modifiche a lifecycle
- prima di fidarsi di un refactor
- prima di eseguire backtest su larga scala

# Workflow
1. Identifica le aree modificate
2. Elenca casi normali attesi
3. Elenca edge case probabili
4. Verifica regressioni su classificazione, estrazione, linking e lifecycle
5. Controlla mismatch tra schema, parser e DB
6. Valuta la qualità del diff
7. Produci report severità e fix

# Output richiesto
Restituisci sempre:
- problemi trovati
- severità
- motivo del rischio
- file coinvolti
- test mancanti
- fix suggeriti

# Regole
- separa bug certi da rischi probabili
- non limitarti a dire "sembra ok"
- sii specifico sui casi da testare
- privilegia i problemi che alterano eventi canonici o stato posizione