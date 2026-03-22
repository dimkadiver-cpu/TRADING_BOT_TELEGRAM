# Exchange Precision Engine

## Scopo

Questo componente normalizza quantità e prezzi prima dell'invio degli ordini.

## Dati richiesti per simbolo

- `price_tick`
- `qty_step`
- `min_qty`
- `min_notional`

## Regole logiche

### Quantità
La quantità deve essere arrotondata allo step consentito dall'exchange.

### Prezzo
Il prezzo deve essere allineato al tick consentito.

### Notional minimo
Se il valore dell'ordine è sotto il minimo consentito, l'ordine non deve passare.

## Nota

Questo blocco non decide la strategia del trade. Applica soltanto i vincoli reali dell'exchange.
