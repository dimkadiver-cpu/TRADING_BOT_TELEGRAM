# Parser - Architettura e Flusso Runtime

## 1. Obiettivo del layer parser

Il parser trasforma un messaggio Telegram grezzo in un output strutturato, da usare per:

- classificazione messaggio (`NEW_SIGNAL`, `UPDATE`, `INFO_ONLY`, `UNCLASSIFIED`);
- estrazione entita operative (symbol, entry, stop, tp, target, ecc.);
- derivazione intents semantici;
- serializzazione verso storage parser e layer downstream.

Nel codice attuale convivono due contratti:

- `TraderParseResult` (legacy attivo, usato da Router/validation/phase4).
- `CanonicalMessage v1` (shadow/native, salvato su `parse_results_v1`).

## 2. Flusso end-to-end (runtime reale)

```text
raw_messages (QueueItem dal listener)
  -> MessageRouter._route_inner(...)
  -> resolve trader + eligibility + blacklist checks
  -> get_profile_parser(trader_id)
  -> profile.parse_message(text, context) -> TraderParseResult
  -> validate(result) [coherence layer]
  -> parse_results upsert (legacy record)
  -> opzionale parse_results_v1 upsert (canonical)
     - native: profile.parse_canonical(...)
     - shadow: canonical_v1.normalizer.normalize(result, context)
  -> opzionale phase4 (operation rules + target resolver + execution update)
```

## 3. Punto di integrazione nel Router

Il Router costruisce `ParserContext` con:

- `trader_code`, `message_id`, `reply_to_message_id`, `channel_id`;
- `raw_text`, `reply_raw_text`;
- `extracted_links`, `hashtags`.

Poi invoca il profilo trader registrato in `src/parser/trader_profiles/registry.py`.

Se il profilo non esiste, il Router salva uno `SKIPPED` su `parse_results` con `message_type=UNCLASSIFIED`.

## 4. Doppio binario output (legacy + canonical v1)

### 4.1 Legacy (sempre usato dal flusso operativo)

- `profile.parse_message(...) -> TraderParseResult`
- validazione coerenza (`src/validation/coherence.py`)
- persistenza in `parse_results` (`src/storage/parse_results.py`)

### 4.2 Canonical v1 (parallelo, non bloccante)

Se `ParseResultV1Store` e cablato nel Router:

- profilo v1-native: `parse_canonical(...)` diretto;
- altrimenti: normalizzazione shadow da `TraderParseResult` a `CanonicalMessage`.

Errori nel path canonical v1 non bloccano il path legacy: vengono registrati in `normalizer_error`.

## 5. Stato architetturale attuale

- parser principale: profili trader specifici (`trader_a`, `trader_b`, `trader_c`, `trader_d`, `trader_3`);
- classificazione marker-based: `RulesEngine`;
- contratto canonico v1: presente e testato;
- bridge legacy -> envelope -> canonical: attivo (`adapters/legacy_to_event_envelope_v1.py`, `canonical_v1/normalizer.py`);
- phase4 dipende ancora dal payload legacy (`TraderParseResult`) validato.

