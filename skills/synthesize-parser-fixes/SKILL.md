---
name: synthesize-parser-fixes
description: Usa questa skill quando hai uno o piu report di audit parser e devi consolidarli, trovare pattern ricorrenti, distinguere fix globali da fix locali al singolo trader, proporre soluzioni minimali e preparare istruzioni operative per chi eseguira le modifiche.
---

# Obiettivo

Trasformare uno o piu report di audit in una strategia di fix pulita:
- cosa e sistemico
- cosa e trader-specifico
- cosa e solo report stale
- quali modifiche conviene fare prima

# Quando usarla

- dopo un audit CSV/DB
- quando ci sono molti `raw_message_id` con problemi simili
- quando non e chiaro se intervenire nel layer comune o nel profilo trader
- quando serve un handoff tecnico per un esecutore finale

# Input attesi

- uno o piu report prodotti da `audit-parser-csv-chain`
- eventuali note del reviewer
- opzionalmente elenco dei trader coinvolti

# Workflow

1. Raggruppa i casi per pattern.
2. Per ogni pattern decidi se e:
   - globale
   - locale a trader
   - dato stale / rigenerazione report
   - non bug
3. Valuta la soluzione minima sicura.
4. Ordina gli interventi per priorita e rischio.
5. Prepara istruzioni operative per l’esecutore finale.

# Criteri di classificazione

Un problema e **globale** se tocca:
- targeting comune
- resolver / reply-chain
- exporter report
- normalizzazione shared
- intent/action semantics condivise

Un problema e **locale** se tocca:
- vocabolario trader-specifico
- regex del profilo
- override di precedence solo di un trader
- eccezioni linguistiche di un trader

Un problema e **stale** se:
- il parser attuale produce output corretto
- ma `parse_results` o CSV riportano ancora il vecchio risultato

# Formato soluzioni

Per ogni pattern usa questo schema:

```text
pattern:
gravita:
ambito: global | local | stale
trader coinvolti:
sintomo:
causa probabile:
soluzione proposta:
rischio regressione:
test da aggiungere:
```

# Istruzioni per esecutore finale

Genera una sezione finale chiamata `Istruzioni esecutore`.

Per ogni task includi:
- file candidati
- modifica minima attesa
- comportamento atteso dopo fix
- test da eseguire
- se serve rigenerare report o replay

Usa task brevi e indipendenti.

# Regole

- preferire fix minimi e spiegabili
- separare rigorosamente fix globali da fix locali
- non mischiare nel medesimo task una refactor grande e un bugfix piccolo
- indicare quando basta rigenerare `parser_test` invece di cambiare codice
- se ci sono dubbi, esplicitarli come assunzioni
