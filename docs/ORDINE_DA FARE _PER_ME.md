1. Sistemare la parte esecutoria del bot.

    1) verificare il comportamento bot freqtrade:

    Caso_1: odrine singolo Limit - deve piazzare un ordine limit

    Caso_2: ordine doppio limite E1 E2 - deve piazzare due ordini limit

    Caso_3: ordine doppio  E1 - Market; E2 e sucessivi Limit, deve aprire un ordine al market  deve aprire ordine Limit

    Come si puo fare?

    2) Vorrei  fare un sistema si di inzione  di messaggi fetizzii in db  per verificare il funzionamento del bot, senza attendere ore la comparsa dal telegram ma  farli io e testare vari casi con diversi variabili in drymode.

    3) ~~Verificare la persistanza dei disgeni (sl. tp ecc) su grafico di un trade gia chiuso, forse è legato alla strategia? comportmanto ateso il grafico del trade. in pratica trde chiuso, poi un altro aperto, ma h su grafico i segni (sl, tp) du un latro trade gia chiuso~~ Residuo, storico di trade aperti (punti), forse serve fare vedere tp ??

    4) quando fila ordine di ingreso al mercato, nei indicatori compaiona entri zona come fosse limit, aggiungere la media per due ordini fillati?


















2. Sistemare Sistema 2 -
    - Migrare su app separata!!!
