# Parser — Flusso completo

Il parser converte testo grezzo da Telegram in `CanonicalMessage` v1, il contratto unico verso il resto del sistema.

---

## Flusso end-to-end (produzione)

Il router esegue il percorso completo spec v1 per ogni profilo che espone `parse()`.

```
testo grezzo + ParserContext
        │
        ▼
  [Router] profile.parse()           ← percorso principale (PARSER_USE_PARSED_MESSAGE=1)
        │
        ▼
  1. _preprocess()
        │  normalizzazione testo, split righe,
        │  estrazione link Telegram, hashtag
        ▼
  2. RulesEngine.classify()          ← semantic_markers.json + rules.json (merged)
        │  score per categoria: NEW_SIGNAL | UPDATE | INFO_ONLY | UNCLASSIFIED
        │  algoritmo: Σ(strong×1.0) + Σ(weak×0.4) + combination_rules boost
        ▼
  3. _extract_targets()
        │  cerca REPLY (reply_to_message_id), TELEGRAM_LINK (t.me/…),
        │  EXPLICIT_ID (Signal ID:), SYMBOL (ticker pattern)
        ▼
  4. _resolve_global_target_scope()
        │  cerca scope globali: ALL_LONGS, ALL_SHORTS, ALL_POSITIONS, …
        ▼
  5. _classify_message()
        │  prende il ClassificationResult dal RulesEngine
        │  applica correzioni post-hoc:
        │    UNCLASSIFIED + intents + target → UPDATE
        │    UPDATE senza target + solo stop-mgmt intents → UNCLASSIFIED
        ▼
  6. _extract_intents()
        │  RulesEngine.detect_intents_with_evidence() → IntentCandidate[]
        │  SemanticResolver:
        │    a. disambiguation_rules  (prefer / suppress tra intenti sovrapposti)
        │    b. compatibility_engine  (specifico-vs-generico, esclusivi, compatibili)
        │    c. context_resolution    (valida intent contro storico target)
        │    d. primary_intent        (per precedenza dichiarata)
        ▼
  7. _extract_reported_results()
        │  cerca risultati numerici (R, %, testo) per TP_HIT / SL_HIT / FINAL_RESULT
        ▼
  8. _extract_entities()              ← TraderAExtractors (regex per profilo)
        │  SIGNAL → symbol, side, entries (EntryLeg[]), SL, TP[], risk
        │  UPDATE → stop price, close%, TP list, entry prices, …
        ▼
        │  → ParsedMessage (validation_status: PENDING)
        ▼
  [Router] IntentValidator.validate()
        │  HistoryBackedIntentValidator — valida intenti contro storico DB
        │  (PassthroughIntentValidator se db_path non disponibile)
        ▼
  [Router] DisambiguationEngine.apply()
        │  ProfileRulesDisambiguationEngine — applica disambiguation_rules dal profilo
        ▼
  [Router] IntentTranslator.translate()
        │  ProfileCanonicalMessageTranslator → CanonicalMessage
        ▼
  9. build CanonicalMessage
        │  primary_class:  SIGNAL | UPDATE | REPORT | INFO
        │  parse_status:   PARSED | PARTIAL | UNCLASSIFIED | ERROR
        │  signal:         SignalPayload (entries, SL, TPs, side, risk)
        │  update:         UpdatePayload (operations canoniche)
        │  report:         ReportPayload (events: TP_HIT, SL_HIT, ENTRY_FILLED, …)
        │  targeting:      Targeting (refs + scope)
        │  warnings:       list[str]
        ▼
  CanonicalMessage  →  parse_results_v1 (persistito dal router)
```

---

## Componenti principali

### `RulesEngine` — `rules_engine.py`

Caricato a runtime con il merge di `semantic_markers.json + rules.json` (via `_load_merged_rules()`) e produce:

- **`classify(text)`** → `ClassificationResult`
  - Calcola score per `new_signal`, `update`, `info_only` tramite `classification_markers`
  - Applica `combination_rules` (boost se più marker co-presenti)
  - Vince la categoria con score massimo; se tutto zero → `UNCLASSIFIED`
- **`detect_intents_with_evidence(text)`** → `list[IntentDetectionMatch]`
  - Scansiona `intent_markers` (strong/weak) per ogni intent dichiarato
- **`is_blacklisted(text)`** → blocca messaggi con marker espliciti di blacklist

### `SemanticResolver` — `shared/semantic_resolver.py`

Applicato sugli intent candidati dopo il rilevamento:

1. **Disambiguation** (`disambiguation_rules_schema`): risolve conflitti tra intenti sovrapposti
   - `prefer`: tra due intenti sceglie quello più specifico se il testo lo conferma
   - `suppress`: elimina un intento se un altro più preciso è presente
2. **Compatibility** (`compatibility_engine`): verifica coppie di intenti
   - `specific_vs_generic`: tieni il più specifico
   - `exclusive`: tieni uno solo
   - `compatible`: entrambi permessi
3. **Context resolution** (`context_resolution_engine`): valida intenti contro lo storico del target
   - es. `EXIT_BE` richiede `MOVE_STOP_TO_BE` nello storico del segnale referenziato
4. **Primary intent**: selezionato per ordine di precedenza dichiarato in `rules.json → primary_intent_precedence`

### `TraderAExtractors` — `trader_profiles/trader_a/extractors.py`

Estrazione entità tramite regex specifiche per trader_a:

| Campo | Regex principale |
|-------|-----------------|
| symbol | `[A-Z0-9]{1,24}(USDT\|USDC\|USD\|BTC\|ETH)(\.P)?` |
| entry (market) | `вход с текущих` / `вход: <price>` |
| entry (limit) | `entry a/b`, `a (лимит): <price>` |
| stop loss | `sl: <price>` / `стоп: <price>` |
| take profit | `tp1: <price>`, `tp2: <price>`, … |
| stop move | `move sl to <price>`, `стоп переношу на <price>` |
| close % | `фикс 50%`, `частично` |
| result | `+2R`, `+1.5%`, testo libero |

### `CanonicalMessage` — `canonical_v1/models.py`

Contratto di output. Non modificare la struttura.

```python
CanonicalMessage(
    primary_class:  SIGNAL | UPDATE | REPORT | INFO
    parse_status:   PARSED | PARTIAL | UNCLASSIFIED | ERROR
    trader_id:      str
    raw_context:    RawContext          # testo grezzo, reply_to_id, links
    targeting:      Targeting           # refs (REPLY/LINK/ID/SYMBOL) + scope
    signal:         SignalPayload | None
    update:         UpdatePayload | None
    report:         ReportPayload | None
    warnings:       list[str]
)
```

**SignalPayload** contiene:
- `entries: list[EntryLeg]` con struttura `ONE_SHOT | TWO_STEP | RANGE | LADDER`
- `stop_loss: StopLoss`
- `take_profits: list[TakeProfit]`
- `side: LONG | SHORT`
- `risk: RiskHint | None`

**UpdatePayload** contiene una lista di operazioni canoniche:
- `SET_STOP` — nuovo livello stop (price, ENTRY, TP_LEVEL)
- `CLOSE` — chiusura parziale o totale
- `CANCEL_PENDING` — cancella ordini limite pendenti
- `MODIFY_ENTRIES` — aggiungi / rientra / modifica entry
- `MODIFY_TARGETS` — aggiorna take profit

---

## File di configurazione per profilo

Ogni profilo in `trader_profiles/<trader_X>/` ha:

| File | Usato da | Scopo |
|------|----------|-------|
| `semantic_markers.json` | `_load_merged_rules()` | vocabolario: classification_markers, intent_markers, field_markers, side, entry_type, global_target, blacklist |
| `rules.json` | `_load_merged_rules()` | logica: combination_rules, disambiguation_rules, intent_compatibility, context_resolution_rules, primary_intent_precedence |
| `extractors.py` | `profile.py` | regex di estrazione entità specifiche per trader |
| `profile.py` | `registry.py` | implementa `parse()` → `ParsedMessage` e `parse_canonical()` |

I due file vengono mergiati a runtime (`{**semantic_markers, **rules}`) e caricati in un unico `RulesEngine`. `rules.json` ha precedenza su `semantic_markers.json` in caso di chiave duplicata.

`field_markers` e `extraction_markers` sono presenti in `semantic_markers.json` come metadati dichiarativi ma **non letti dal RulesEngine** — sono disponibili agli `extractors.py` dei profili come guida all'estrazione.

---

## Registry e risoluzione profilo

```python
# src/parser/trader_profiles/registry.py
parser = get_profile_parser("trader_a")   # alias: "a", "ta"

# Percorso produzione ParsedMessage: parse() → shared/runtime.py → validator → disambiguation → translate()
parsed: ParsedMessage = parser.parse(text, context)

# Percorso diretto (test, replay): salta validator e disambiguation
result: CanonicalMessage = parser.parse_canonical(text, context)
```

Profili registrati: `trader_a`, `trader_b`, `trader_c`, `trader_d`, `trader_3`.
Tutti implementano `parse()` (spec v1) e `parse_canonical()` (accesso diretto).

---

## Componenti del router (`src/telegram/router.py`)

Il router orchestra i tre layer post-parse:

| Componente | Classe predefinita | Attivazione |
|---|---|---|
| `IntentValidator` | `HistoryBackedIntentValidator(db_path)` | auto quando `db_path` è disponibile |
| `IntentValidator` | `PassthroughIntentValidator` | fallback quando nessun DB |
| `DisambiguationEngine` | `ProfileRulesDisambiguationEngine` | sempre attivo |
| `IntentTranslator` | `ProfileCanonicalMessageTranslator` | sempre attivo |

Flag di controllo: `PARSER_USE_PARSED_MESSAGE` (default `1`). Impostare a `0` per disabilitare il percorso spec e usare solo `parse_canonical()` diretto.

---

## Sorgenti di verità per l'architettura

- Schema canonico: `canonical_v1/models.py`
- Tassonomia intenti: `canonical_v1/intent_taxonomy.py`
- Regole schema JSON: `trader_profiles/shared/rules_schema.py`
- Test smoke: `trader_profiles/<trader>/tests/`
- Replay su dati reali: `parser_test/scripts/replay_parser.py`
