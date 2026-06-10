# Trader Resolution v2 — Design Spec

**Data:** 2026-06-10  
**Stato:** Approvato, pronto per implementazione

---

## Contesto

Il sistema acquisisce messaggi da canali Telegram che possono essere:
- **Single-trader** — un solo trader per fonte (canale o topic)
- **Multi-trader** — più trader postano sulla stessa fonte

Per i canali single-trader la risoluzione è triviale (config). Per i multi-trader serve risoluzione dinamica basata sul contenuto del messaggio e sulla catena di reply.

---

## Casi d'uso supportati

| Tipo fonte | Esempio | Risoluzione |
|---|---|---|
| Canale — un trader | canale dedicato | config statico |
| Canale — multi-trader | canale broadcast condiviso | dinamica |
| Gruppo + topic specifico | topic dedicato per trader | config statico |
| Gruppo + topic multi-trader | topic condiviso tra più trader | dinamica |

`from_id` (sender Telegram) non viene usato — inaffidabile in presenza di bot aggregatori.

---

## Configurazione — channels.yaml

Ogni entry dichiara se il topic è single-trader (`trader_id` valorizzato) o multi-trader (`trader_id: null`).

### Single-trader (invariato)

```yaml
- chat_id: -1003722628653
  topic_id: 3
  label: "PifSignal_A"
  active: true
  trader_id: trader_a
  parser_profile: trader_a
  blacklist: []
```

### Multi-trader (nuovo)

```yaml
- chat_id: -1003722628653
  topic_id: 9
  label: "MultiTopic_X"
  active: true
  trader_id: null          # segnala risoluzione dinamica
  parser_profile: null     # null → usa resolved_trader_id come profilo (ogni trader il suo)
                           # valorizzato → tutti i messaggi del topic usano quel profilo
                           #   (utile quando più trader condividono lo stesso formato)
  resolution:
    max_depth: 5           # max livelli di risalita reply chain (default 5)
    aliases:               # per-topic, nessun fallback globale
      trader#a: trader_a
      trader#b: trader_b
      trader#3: trader_3
  blacklist: []
```

**Regole alias:**
- Scoped al topic — nessun fallback globale
- Le chiavi seguono la normalizzazione di `trader_tags.py` (forma canonica `trader#x`)
- Il cirillico viene normalizzato al latino prima del lookup (`С→c`, `А→a`, ecc.)
- Lo stesso tag può mappare a trader diversi in topic diversi

`telegram_source_map.json` viene abbandonato — tutto gestito in `channels.yaml`.

---

## Pipeline di risoluzione

Un singolo `TraderResolver` sostituisce `EffectiveTraderResolver` e `RuntimeV2TraderResolver`.

### Ordine di priorità (per messaggio)

```
1. Config statico
   └─ entry.trader_id valorizzato → stop, trader certo

2. Tag nel testo del messaggio corrente
   └─ aliases per-topic → trader trovato → stop
   └─ pattern_extractors.py (hardcoded, fallback per casi speciali) → stop

3. Reply diretto (reply_to_message_id)
   └─ leggi resolved_trader_id ?? source_trader_id del parent
   └─ se None → cerca alias nel testo del parent
   └─ se None → risali di un livello (max depth)
   └─ parent non in DB → stop → unresolved

4. Link singolo nel testo (t.me link)
   └─ stessa logica del reply diretto

5. Link multipli nel testo
   └─ tutti concordi sullo stesso trader → usa quel trader
   └─ trader diversi → ambiguous → review

6. Nessun segnale → unresolved → review
```

### Regola di stop nella reply chain

```
parent trovato + trader risolto  → stop, usa quel trader
parent trovato + trader None     → continua (risali di un livello)
parent non in DB                 → stop → unresolved
depth > max_depth                → stop → unresolved
```

### Conflitto tag testo vs reply

Se un messaggio è reply a trader_a ma il testo contiene tag trader_b → **il tag nel testo vince** (step 2 > step 3). Il tag è l'intenzione esplicita del messaggio corrente.

---

## Pattern extractors (casi speciali hardcoded)

Per topic con identificazione derivata da pattern semantici nel testo (es. strategia + timeframe), la logica è hardcoded in `src/telegram/pattern_extractors.py`:

```python
def extract_trader_by_pattern(topic_id: int, text: str) -> str | None:
    if topic_id == 9:
        if "«RSI(2) Коннора»" in text and "интрадей" in text:
            return "trader_rsi_intraday"
        if "«RSI(2) Коннора»" in text and "свинг" in text:
            return "trader_rsi_swing"
    return None
```

Chiamato solo se gli alias non trovano nulla. Nessuna configurazione YAML per questi casi — rimangono nel codice finché non diventano abbastanza comuni da giustificare un meccanismo config.

---

## Storage

### Colonne coinvolte in raw_messages

| Colonna | Scritto quando | Valore |
|---|---|---|
| `source_trader_id` | Ingest (config statico) | `trader_id` se fonte single-trader, `None` se multi-trader |
| `resolved_trader_id` | Dopo risoluzione dinamica | Risultato del `TraderResolver` |
| `resolution_method` | Dopo risoluzione | `source_chat_id`, `source_topic_config`, `content_alias`, `reply_chain`, `link`, ecc. |
| `resolution_detail` | Dopo risoluzione | Dettaglio (es. message_id del parent che ha risolto) |

### Reply chain lookup

Il walker legge `resolved_trader_id ?? source_trader_id` usando `RawMessageRepository` (non `RawMessageStore`). Dopo ogni risoluzione, `resolved_trader_id` viene scritto immediatamente — i reply successivi trovano il parent già risolto senza dover rileggere il testo.

---

## Wiring nel listener

`listener._process_item()` viene aggiornato per chiamare `TraderResolver` invece del lookup diretto su `ChannelConfigResolver`.

Il `RuntimeV2IntakeProcessor` esistente viene allineato o rimosso — elimina la doppia pipeline inconsistente.

---

## Cosa non cambia

- `trader_tags.py` — regex e normalizzazione invariate
- `ChannelConfigResolver` — lookup per-topic invariato
- `channels.yaml` per entry single-trader — nessuna modifica necessaria
- Blacklist globale e per-canale — invariata
- Dedup e ingestion (`RawMessageIngestionService`) — invariati

---

## File toccati (stima)

| File | Azione |
|---|---|
| `config/channels.yaml` | Aggiunge campo `resolution` per topic multi-trader |
| `src/telegram/channel_config.py` | Aggiunge `ResolutionConfig` dataclass, parsing `aliases` e `max_depth` |
| `src/runtime_v2/trader_resolution/channel_config_resolver.py` | Espone `aliases` e `max_depth` per-topic |
| `src/telegram/pattern_extractors.py` | Nuovo — hardcoded pattern rules |
| `src/telegram/trader_resolver.py` | Nuovo — `TraderResolver` unico |
| `src/telegram/effective_trader.py` | Deprecato / rimosso |
| `src/runtime_v2/trader_resolution/resolver.py` | Deprecato / rimosso |
| `src/telegram/listener.py` | `_process_item()` chiama `TraderResolver` |
| `src/runtime_v2/intake/processor.py` | Allineato o rimosso |
| `config/telegram_source_map.json` | Abbandonato |
