# Entry Process Notes

## Contesto

Questo file raccoglie promemoria e questioni aperte sul processo di entry lato
bot/runtime.

## Questione aperta principale

Va rivisto il contratto tra:
- selezione del segnale `PENDING`
- costruzione dell'ordine entry
- validazione finale in `confirm_trade_entry()`

## Caso reale osservato

Segnale:
- `raw 2`
- `attempt_key = T_-1003171748254_3469_trader_3`
- entry zone `0.1625 - 0.1635`

Comportamento osservato prima del fix:
- il segnale era interpretato come piano `LIMIT`
- il runtime usava in validazione un `rate` live/proposed circa `0.164248`
- `confirm_trade_entry()` rifiutava l'entry con `ENTRY_PRICE_REJECTED`
- di fatto il pendente `LIMIT` non veniva nemmeno piazzato

## Problema architetturale

Per una entry `LIMIT`, il controllo finale non dovrebbe usare come source of
truth il `rate` live se l'ordine finale viene ancorato a un prezzo esplicito del
piano.

In particolare:
- `custom_entry_price()` gia conosce il prezzo entry finale del piano
- `confirm_trade_entry()` non deve rifiutare il trade usando un prezzo runtime
  diverso da quello del pendente che verra realmente piazzato

## Regola desiderata

Se il segnale e `LIMIT`:
- il prezzo effettivo usato nel gate deve essere il prezzo finale dell'ordine
  pendente (`E1` / prezzo custom finale)
- il `rate` live puo restare un dato diagnostico, ma non deve bloccare da solo
  la creazione del pendente corretto

Se il segnale e `MARKET`:
- il controllo deve continuare a usare il `rate` runtime

## Obiettivo

Evitare casi in cui:
- il segnale e formalmente `LIMIT`
- il bot dovrebbe piazzare un pendente in zona
- ma l'entry viene rifiutata prima per colpa del `rate` live, incoerente con il
  prezzo finale dell'ordine
