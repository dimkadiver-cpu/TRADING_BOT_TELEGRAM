# Flusso Rapido

## In una frase

Il bot legge il messaggio Telegram, salva il raw, decide trader/linking, poi passa al parser che crea un output normalizzato.

## Catena pratica

1. `main.py` avvia listener e dipendenze.
2. `src/telegram/listener.py` riceve i messaggi.
3. `src/telegram/ingestion.py` salva raw message in DB.
4. `src/telegram/effective_trader.py` risolve il trader effettivo.
5. `src/telegram/eligibility.py` valuta se il messaggio è parsabile e se ha link forti.
6. `src/parser/pipeline.py` esegue `MinimalParserPipeline.parse(...)`.
7. `src/parser/dispatcher.py` sceglie la modalità parser (regex / llm / hybrid).
8. `src/parser/normalization.py` costruisce il risultato normalizzato.
9. `src/storage/parse_results.py` salva il risultato parser.

## Punto di arrivo di questa guida

Arriviamo fino alla funzione di parsing (`MinimalParserPipeline.parse`) e alla creazione del payload normalizzato.
