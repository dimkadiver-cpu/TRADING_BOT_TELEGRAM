---
name: prepare-parser-executor-handoff
description: Usa questa skill quando devi convertire una lista di fix parser gia analizzati in un handoff finale eseguibile: task ordinati, file da toccare, test da aggiungere o aggiornare, criteri di accettazione, e note per evitare regressioni o modifiche fuori scope.
---

# Obiettivo

Consegnare a un agente esecutore un piano d’azione pronto, chiaro e a basso rischio.

# Quando usarla

- dopo `synthesize-parser-fixes`
- quando i fix sono stati gia capiti ma non ancora implementati
- quando vuoi ridurre il rischio che l’esecutore allarghi troppo il perimetro

# Input attesi

- sezione `Istruzioni esecutore`
- elenco fix approvati
- eventuali vincoli utente

# Output richiesto

Produrre un handoff finale con questo formato:

```text
Contesto
Obiettivo
Vincoli

Task 1
- file:
- modifica:
- test:
- done quando:

Task 2
- file:
- modifica:
- test:
- done quando:

Rischi residui
Comandi di verifica
```

# Regole per comporre i task

- un task = un problema ben delimitato
- raggruppare solo cambiamenti sullo stesso punto logico
- evitare task che mischiano:
  - parser locale
  - report exporter
  - resolver globale
- indicare sempre i test da eseguire
- indicare quando serve rigenerare:
  - `python parser_test/scripts/generate_parser_reports.py --trader <trader>`

# Criteri di qualita dell’handoff

L’handoff e buono se:
- l’esecutore sa subito dove mettere mano
- non deve reinterpretare il bug da zero
- sa quali regressioni evitare
- sa quando fermarsi

# Vincoli da ricordare

- non proporre refactor larghi se il problema e locale
- non toccare migration o schema se non richiesto
- se il caso e solo `stale_db_result`, chiedere replay/report e non codice
- preservare i flussi gia corretti

# Nota

Questa skill non decide se il fix e giusto. Assume che l’analisi sia gia stata fatta e prepara solo il passaggio sicuro all’implementazione.
