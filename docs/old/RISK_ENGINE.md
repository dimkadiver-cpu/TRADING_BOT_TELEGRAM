# Risk Engine

## Scopo

Il risk engine decide se un segnale puo essere trasformato in un trade attivo senza violare i limiti del sistema.

## Stato implementazione

Questo documento descrive il comportamento atteso del modulo.
Nel codice attuale `src/execution/risk_gate.py` e ancora TODO e il risk engine non e attivo nel runtime.

## Logica base

Il rischio e espresso come percentuale del capitale.

Schema concettuale:

- si parte dall'equity disponibile
- si applica la percentuale di rischio per trade
- si calcola la dimensione posizione in funzione della distanza tra entry e stop

## Controlli principali

### Limiti di portafoglio

- massimo numero trade aperti
- massimo rischio complessivo aperto

Se un nuovo trade supera questi limiti, viene bloccato.

### Regole trader-specific

Ogni trader puo avere:

- rischio per trade diverso
- leva diversa
- modalita di esecuzione diversa

## Output atteso

Il risk engine non invia ordini.

Deve restituire una decisione chiara:

- trade accettato
- trade bloccato
- trade da mettere in attesa

piu le motivazioni utili per audit e debug.
