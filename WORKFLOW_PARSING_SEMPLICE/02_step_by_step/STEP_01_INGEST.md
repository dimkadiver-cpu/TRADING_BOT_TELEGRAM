# Step 1 - Ingestione Messaggio

## Cosa succede

Quando arriva un nuovo messaggio Telegram:

1. il listener lo riceve
2. il testo viene messo in forma utile
3. il raw viene salvato nel DB per audit

## File principali

- `main.py`
- `src/telegram/listener.py`
- `src/telegram/ingestion.py`
- `src/storage/raw_messages.py`

## Perche è importante

Prima di fare parsing, il sistema conserva sempre il messaggio originale (`raw_text`), così si può tracciare ogni decisione.
