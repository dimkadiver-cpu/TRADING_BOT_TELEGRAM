# Risk Engine

## Scopo

Il risk engine decide se un segnale può essere trasformato in un trade attivo senza violare i limiti del sistema.

## Logica base

Il rischio è espresso come percentuale del capitale.

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

Ogni trader può avere:

- rischio per trade diverso
- leva diversa
- modalità di esecuzione diversa

## Output atteso

Il risk engine non invia ordini.

Deve restituire una decisione chiara:

- trade accettato
- trade bloccato
- trade da mettere in attesa

più le motivazioni utili per audit e debug.
