---
name: telegram-ingestion
description: Usa questa skill per lavorare sulla pipeline di ingestione Telegram, trader resolution, eligibility e linking iniziale senza rompere parser e storage.
---

# Obiettivo
Gestire correttamente l’ingresso dei messaggi Telegram nel sistema, mantenendo separati acquisizione, risoluzione trader, eligibility e parsing.

# Quando usarla
- quando si modifica src/telegram/listener.py
- quando si migliora trader resolution
- quando si tocca eligibility o linking
- quando si aggiunge nuova logica pre-parser
- quando si analizzano problemi di unresolved trader o message linkage

# Flusso attuale
Il flusso runtime nella parte Telegram è:

1. ricezione messaggio Telegram
2. filtro chat ammesse
3. effective trader resolution
4. eligibility / strong-link check
5. raw ingestion
6. parser minimale
7. parse result upsert

File chiave:
- src/telegram/listener.py
- src/telegram/ingestion.py
- src/telegram/effective_trader.py
- src/telegram/eligibility.py
- src/telegram/trader_mapping.py

# Responsabilità dei moduli
## listener.py
Coordina il flusso.
Deve rimanere relativamente sottile.

## ingestion.py
Gestisce la persistenza iniziale del messaggio raw.

## effective_trader.py
Determina il trader effettivo usando tag, reply, mapping o altre fonti disponibili.

## eligibility.py
Valuta se il messaggio è processabile o se ha linking sufficientemente forte.

## trader_mapping.py
Gestisce mappe di risoluzione trader e source mapping.

# Regole importanti
- listener.py deve orchestrare, non contenere tutta la business logic
- la risoluzione trader deve avvenire prima del parsing trader-specific
- l’eligibility deve essere esplicita e verificabile
- il messaggio raw va conservato prima di logiche operative più avanzate
- non inserire parsing del testo grezzo dentro effective_trader o eligibility
- non inserire logiche exchange in listener.py

# Linking e risoluzione
Ordine di priorità consigliato per il linking:
1. reply_to_message_id
2. explicit Telegram message link
3. source mapping
4. heuristics per trader + strumento + recency

Se la risoluzione è debole:
- non forzare successi silenziosi
- abbassare confidence
- registrare ambiguità

# Problemi noti
- telegram_source_map.json può essere vuoto o incompleto
- unresolved trader risk elevato se manca reply/tag/source
- listener async con storage sync può diventare fragile sotto carico
- linking debole può produrre update non applicabili

# Cosa non fare
- non far decidere al listener lo stato finale del trade
- non far fare al parser il lavoro di trader resolution
- non far leggere Telegram direttamente ai moduli di execution
- non usare heuristiche nascoste senza segnalarle

# Punti di estensione sicuri
- miglioramenti trader resolution: effective_trader.py / trader_mapping.py
- miglioramenti eligibility/linking: eligibility.py
- nuovi hook dopo parse persistence: listener.py, ma delegando a un servizio separato
- reporting ingestione: modulo read-only dedicato

# Output richiesto
Quando usi questa skill, restituisci sempre:
- fase del flusso coinvolta
- file corretti da toccare
- impatto su trader resolution / eligibility / linking
- rischio di regressione
- test o casi reali da verificare