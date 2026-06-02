- 1. [X] . Nel signal accepted quando vien definito entry, deve rapresentare la policy . esempio se ho range e nella policy ho midlepoit, nel log deve essere entry effetivo aplicato e non rivelato. percje in log ho due entry  ma su exchange uno solo, verificare se logiaca di policy e quella che segue poi aggiornamnento, nel caso di Entri filled, se è uno solo (quello su exchange) o quello in log 

- 2. [Y] Verifica che tutti i segnali sia acettati/ che rifiutati / andati inreviu vengono logati , con eventuale causa-
Gap residuo — enrichment blocks:
  Questi messaggi vengono bloccati nel SignalEnrichmentProcessor con lifecycle_processed=True e non raggiungono mai il lifecycle gate.
  Per loggarli servirebbe un worker separato che scansiona enriched_canonical_messages WHERE enrichment_decision IN ('BLOCK','REVIEW').
  Non lo tocco ora — è un task separato.

- 3. [X]    / quando faccio /trades ho #1 JUPUSDT 📈 LONG | WAITING_ENTRY | NoSL
#2 TRXUSDT 📈 LONG | WAITING_ENTRY | NoSL
#3 SKYUSDT 📈 LONG | WAITING_ENTRY | NoSL
#4 BILLUSDT 📈 LONG | OPEN | NoSL
#5 USUSDT 📈 LONG | OPEN | NoSL
perche ho NOSL ?s

- 4 []. aggiornare ENTRY OPENED/ENTRY UPDATED
    inserire rifrimento quale leg fillata, fee pagata

📊 #5 — ENTRY OPENED
- - - - - - - - - - - - - - - -
USUSDT — 📈 LONG
https://t.me/c/3897279123/318
- - - - - - - - - - - - - - - -
Entry_1 - Filled      <----         
Price: 0.011487
Qty: 176,760
Fee: <----

Position:
Avg entry: 0.011487
Pending: Entry_2 0.011111 Limit
- - - - - - - - - - - - - - - -
Source: exchange


- 5 []. arotondare le decimali di vari valori calcolati