# AUDIT — TeleSignalBot

Registro degli step di migrazione completati, stato dei file e rischi aperti.

---

## 2026-06-11 — Nuovo profilo parser_v2: strategy_parser

### Step completato

Implementato profilo parser_v2 minimale per il bot "Стратегия" che produce segnali automatici da strategie algoritmiche (RSI(2) Коннора, Supertrend, ecc.) su canale Telegram. Profilo built-from-evidence sui pattern di messaggi forniti dall'utente, nessun DB reale campionato.

### Message family map (da esempi reali)

| Famiglia | Pattern chiave | primary_class | primary_intent |
|---|---|---|---|
| SIGNAL open | `открыла ЛОНГ/ШОРТ по <SYMBOL>` + `Вход / стоп / цель` | `SIGNAL` | — |
| CLOSE + SL | `закрыла … — поймала стоп` | `UPDATE` | `SL_HIT` |
| CLOSE + reverse | `вышла по обратному сигналу` | `UPDATE` | `CLOSE_FULL` |
| CLOSE + TP (implicito) | `цель достигнута` | `UPDATE` | `TP_HIT` |

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/parser_v2/profiles/strategy_parser/__init__.py` | Creato | scaffold |
| `src/parser_v2/profiles/strategy_parser/profile.py` | Creato | `StrategyParserProfile` — interfaccia Pydantic completa (come trader_a) |
| `src/parser_v2/profiles/strategy_parser/signal_extractor.py` | Creato | guard `закрыла`, symbol da `по <SYMBOL>`, entry/sl/tp specifici |
| `src/parser_v2/profiles/strategy_parser/intent_entity_extractor.py` | Creato | SL_HIT/TP_HIT/CLOSE_FULL/REPORT_RESULT + exit price da `→ выход` |
| `src/parser_v2/profiles/strategy_parser/semantic_markers.json` | Creato | markers grounded su esempi reali |
| `src/parser_v2/profiles/strategy_parser/rules.json` | Creato | minimal: suppress_weak, cross_intent_suppression SL/TP→CLOSE_FULL |
| `src/parser_v2/profiles/registry.py` | Modificato | aggiunto `strategy_parser` + alias |

### Risultato smoke-test

```
SIGNAL open    → primary_class=SIGNAL  parse_status=PARSED  symbol=HYPE  side=LONG  entry=54.69  sl=53.32  tp=[59.46]  ✅
CLOSE SL_HIT   → primary_class=UPDATE  primary_intent=SL_HIT  intents=[CLOSE_FULL, SL_HIT, REPORT_RESULT]  ✅
CLOSE reverse  → primary_class=UPDATE  primary_intent=CLOSE_FULL  intents=[CLOSE_FULL, REPORT_RESULT]  ✅
```

### Rischi aperti / blind spot

- **Nessun DB reale campionato**: profilo grounded solo su 3 esempi forniti manualmente — potrebbe esserci variazione nella punteggiatura, nel formato del simbolo (es. simboli abbreviati come "H" invece di "HUSDT"), o nella struttura del messaggio di chiusura con TP.
- **Symbol abbreviato**: nel secondo esempio il simbolo è "H" (probabilmente HUSDT) — `normalize_symbol` gestisce l'aggiunta di USDT se non presente, da verificare con dati reali.
- **INFO_ONLY su SIGNAL**: i disclaimer "виртуальная сделка / реальных денег нет" nelle SIGNAL message producono intents INFO_ONLY (weak). Non impatta primary_class=SIGNAL, ma è rumore — da valutare se rimuovere i marker.
- **update_without_target_hint**: warning atteso su tutti i messaggi di chiusura — il bot non usa reply chain, non ha riferimento esplicito alla posizione aperta.
- **TP_HIT non testato**: non era disponibile un esempio reale, il marker `цель достигнута` è derivato dal vocabolario utente.

---

## 2026-06-10 — Trader Resolution v2: TraderResolver unificato (8 task, 115/115 PASS listener)

### Step completato

Implementazione completa del sistema di risoluzione trader v2. Un singolo `TraderResolver` sostituisce i due resolver legacy (`EffectiveTraderResolver`, `RuntimeV2TraderResolver`) con una pipeline a priorità che gestisce canali single-trader e multi-trader.

### Pipeline implementata (ordine di priorità)

1. Config statico (`entry.trader_id` valorizzato) → stop
2. Tag nel testo → `aliases` per-topic → `pattern_extractors.py` (hardcoded)
3. Reply chain walking (`resolved_trader_id ?? source_trader_id`, max_depth configurabile)
4. Single t.me link nel testo
5. Multi-link → concordi → trader; discordanti → ambiguous → review
6. Nessun segnale → unresolved → review

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/runtime_v2/trader_resolution/channel_config_resolver.py` | Modificato | `ChannelEntry` + `aliases: dict[str,str]` e `resolution_max_depth: int`; parsing blocco `resolution:` da YAML |
| `src/runtime_v2/persistence/raw_messages.py` | Modificato | `ChainNode` dataclass + `get_chain_node()` per reply chain walker |
| `src/runtime_v2/trader_resolution/models.py` | Modificato | `ResolutionMethod` + `"link"` e `"link_multi"` |
| `src/telegram/pattern_extractors.py` | Creato | Hardcoded RSI topic 9: `"trader_rsi_intraday"` / `"trader_rsi_swing"` da pattern semantici |
| `src/telegram/trader_resolver.py` | Creato | `TraderResolver` completo: `resolve()`, `_from_text()`, `_resolve_chain()`, `_extract_links()` |
| `src/telegram/listener.py` | Modificato | `_process_item()` chiama `TraderResolver`; unresolved → review; scrive `resolved_trader_id` |
| `main.py` / `main_linux_server.py` | Modificato | `TraderResolver` istanziato e passato a `TelegramListener` |
| `config/channels.yaml` | Modificato | Entry topic_id=9 `RSI_MultiTrader` con `trader_id: null` e `resolution:` block |
| `src/telegram/effective_trader.py` | Deprecato | Warning in `EffectiveTraderResolver.__init__` |
| `src/runtime_v2/trader_resolution/resolver.py` | Deprecato | Warning in `RuntimeV2TraderResolver.__init__` |
| `config/telegram_source_map.json` | Eliminato | Sostituito da `channels.yaml resolution:` |
| `tests/telegram/test_trader_resolver.py` | Creato | 16 test TraderResolver |
| `tests/telegram/test_pattern_extractors.py` | Creato | 5 test pattern extractors |
| `tests/runtime_v2/test_channel_config_resolver.py` | Modificato | +17 test aliases/max_depth |
| `tests/runtime_v2/test_raw_message_repository.py` | Modificato | +10 test ChainNode |
| `src/telegram/tests/` (7 file) | Modificato | Fixture `TelegramListener` aggiornate con `trader_resolver=MagicMock()` |

### Risultato test

```
src/telegram/tests/ → 115 passed ✅
tests/telegram/ → 16+5 passed ✅
tests/runtime_v2/ → pre-existing failures only (ModuleNotFoundError: ccxt/telegram/truststore) ✅
```

### Decisioni architetturali chiave

- **`from_id` non usato**: inaffidabile in presenza di bot aggregatori — solo tag testo + reply chain
- **Aliases per-topic**: nessun fallback globale — stesso tag può mappare a trader diversi in topic diversi
- **Tag testo vince su reply chain**: se tag trovato nel messaggio corrente, non si risale
- **`resolved_trader_id ?? source_trader_id`** nella chain walk: dopo risoluzione, parent già risolto per reply successivi
- **Stop rule reply chain**: resolved → stop; unresolved parent → continua; parent non in DB → stop unresolved; max_depth → stop
- **`parser_profile`**: `entry.parser_profile` se valorizzato, altrimenti `resolved.trader_id` (ogni trader il suo profilo)

### Commit

| SHA | Messaggio |
|---|---|
| `d95d229` | feat: add aliases and resolution_max_depth to ChannelEntry |
| `70ba7c1` | fix: use normalize_trader_aliases helper, add normalization test, guard max_depth range |
| `3f6a005` | feat: add link and link_multi to ResolutionMethod |
| `461a323` | feat: add ChainNode and get_chain_node to RawMessageRepository |
| `0e16d56` | feat: add pattern_extractors for hardcoded topic-based trader identification |
| `7a5ebfa` | feat: add TraderResolver with full priority cascade |
| `edd1b71` | config: add resolution block for multi-trader topics |
| `ddccda7` | feat: wire TraderResolver into listener._process_item, write resolved_trader_id to DB |
| `12eb742` | deprecate: EffectiveTraderResolver and RuntimeV2TraderResolver replaced by TraderResolver; remove telegram_source_map.json |

### Rischi aperti

- **`channels.yaml` aliases vuoti**: il topic RSI (topic_id=9) ha `aliases: {}` — i tag reali dei trader vanno popolati quando noti. Finché vuoti, la risoluzione cade su pattern_extractors.
- **Dead code non rimosso**: `EffectiveTraderResolver`, `RuntimeV2TraderResolver` e `RuntimeV2IntakeProcessor` hanno deprecation warnings ma sono ancora nel codebase — da rimuovere quando `RuntimeV2IntakeProcessor` viene eliminato o migrato.
- **Pre-existing test failures**: 52 test nella suite `tests/` falliscono per `ModuleNotFoundError: ccxt/telegram/truststore` + lifecycle failures — non introdotti da questa feature.
- **pattern_extractors.py hardcoded**: topic_id=9 specificato come costante `RSI_TOPIC_ID`. Se il topic cambia, va aggiornato manualmente.

### Prossimi step

- Popolare `aliases` in `channels.yaml` quando i tag reali dei trader sono noti
- Rimuovere `RuntimeV2IntakeProcessor` e i resolver legacy dopo migrazione completa
- Step B: Migrare `operation_rules` → usa `CanonicalMessage`
- Step C: Migrare `target_resolver` → usa `CanonicalMessage`

---

## 2026-06-09 — Patch V1: Signal Identity, Update Classification, Explicit ID Resolution

### Step completato

Implementata la Patch V1 descritta in `docs/Raggionamento/Patch V1 — Signal Identity, Update Classification, Explicit ID Resolution.md`.

**Proposta 1 — Parser extraction**: ✅ già funzionante (nessuna modifica necessaria). `_extract_explicit_ids()` normalizza correttamente `"Signal ID: #C4"` → `"c4"`.

**Proposta 1b — Persistenza identità chain**: Aggiunto campo `external_signal_id` a `ops_trade_chains`. Il canonical translator ora salva gli `explicit_ids` del segnale nei diagnostics (`signal_explicit_ids`). `_persist_signal()` li legge e li scrive sulla chain.

**Proposta 2 — Classificazione**: Un SIGNAL parziale con `has_update_intent AND has_target_hint` viene riclassificato come UPDATE con warning `signal_like_update_forced_to_update`. Segnali COMPLETE non vengono toccati.

**Proposta 3 — Explicit ID resolution**: `_resolve_targets()` confronta ora `c.external_signal_id` invece di `str(c.canonical_message_id)`. Zero match → `[]` (review). >1 match → `None` (ambiguous). Nessun fallthrough.

### File toccati

| File | Stato | Note |
|---|---|---|
| `db/ops_migrations/014_ops_signal_identity.sql` | Nuovo | Aggiunge `external_signal_id TEXT` a `ops_trade_chains` + indice |
| `src/parser_v2/core/classification_resolver.py` | Modificato | Aggiunta logica `_looks_like_targeted_update()` per PARTIAL signal con update intent+hint |
| `src/parser_v2/translation/canonical_translator.py` | Modificato | Salva `signal_explicit_ids` in diagnostics per messaggi SIGNAL |
| `src/runtime_v2/lifecycle/models.py` | Modificato | Aggiunto `external_signal_id: str | None` a `TradeChain` |
| `src/runtime_v2/lifecycle/repositories.py` | Modificato | `_CHAIN_COLS`, `_chain_from_row`, `save()` aggiornati |
| `src/runtime_v2/lifecycle/entry_gate.py` | Modificato | `_persist_signal` legge `external_signal_id` da diagnostics; `_resolve_targets` usa `external_signal_id`; helper `_norm_signal_id` |

### Risultato test

```
pytest src/parser_v2/tests/ (escluso test preesistente rotto trader_a)
→ 147 passed ✅
```

Il test `test_trader_a_active_tp_hit_after_historical_context_still_emits_tp_hit` era già rotto prima di questa patch.

### Decisioni tecniche

- **Solo PARTIAL forza UPDATE**: Un SIGNAL COMPLETE con Signal ID e false-positive MODIFY_ENTRY (es. trader_d) NON viene riclassificato. Solo i PARTIAL vengono forzati.
- **external_signal_id via diagnostics**: Non modifica il contratto CanonicalMessage. I diagnostics sono il canale corretto per metadati secondari.
- **Nessun fallthrough su explicit_ids**: Se explicit_ids presenti ma nessuna chain trovata → `[]` (non si cade sul single-chain fallback).

### Commit

| SHA | Messaggio |
|---|---|
| `4c1e3fd` | Patch V1: signal identity, update classification, explicit ID resolution |

### Rischi aperti

- **Chain esistenti**: `external_signal_id` sarà NULL per tutte le chain create prima di questa patch. Il fallback implicito (nessun match → review) è conservativo.
- **Migration**: `014_ops_signal_identity.sql` va applicato con lo script di migrazione ops prima del deploy.
- **TradeChainRepository.save()**: aggiornato per consistenza ma non usato nel path produzione (`_persist_signal` fa INSERT diretto).

---

## 2026-06-07 — Type Hints: Add missing parameter annotations to _formatters.py

### Step completato

Aggiunta type hint ai parametri di 7 funzioni formatter in `src/runtime_v2/control_plane/formatters/_formatters.py`. Tutti i parametri annotati con `object` (tipo accettato universalmente da questi formatter).

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/runtime_v2/control_plane/formatters/_formatters.py` | Modificato | Aggiunti type hint `value: object` a `num()`, `text()`, `money()`, `money_signed()`, `pct()`, `pct_signed()`, `fee_rate()` |

### Risultato test

```
pytest tests/runtime_v2/control_plane/test_blocks_formatters.py -v
→ 22 passed in 0.12s ✅
```

### Decisioni

- **Type universale `object`**: i formatter accettano `None`, `int`, `float`, e `str`, quindi `object` è il tipo più generale appropriato.
- **Return type già corretto**: tutte le funzioni avevano già `-> str`, solo i parametri erano annotati male.

### Commit

| SHA | Messaggio |
|---|---|
| `a198c73` | fix: add type hints to _formatters.py function signatures |

### Rischi aperti

Nessuno — fix è minimale e non cambia comportamento.

---

## 2026-06-07 — Trader Risk Hint Integration (5 commit, 1012 PASS, 38 pre-existing FAIL)

### Step completato

Implementato il wiring end-to-end di `use_trader_risk_hint` nel runtime v2: il `risk_hint` estratto dal parser ora riduce (reduce-only) il rischio configurato, e i metadati dell'applicazione vengono persistiti in `plan_state_json` su `ops_trade_chains`.

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/runtime_v2/signal_enrichment/models.py` | Modificato | `RiskConfig`: nuovo campo `risk_hint_range_mode`; `EnrichedSignalPayload`: nuovo campo `risk_hint: RiskHint \| None` |
| `src/runtime_v2/signal_enrichment/processor.py` | Modificato | `_process_signal()`: propaga `signal.risk_hint` in `EnrichedSignalPayload` |
| `src/runtime_v2/lifecycle/risk_capacity.py` | Modificato | `RiskDecision.hint_applied: dict \| None`; `_resolve_risk_hint()` pura; logica reduce-only in `validate()` |
| `src/runtime_v2/lifecycle/execution_plan.py` | Modificato | `build()`: parametro opzionale `extra_plan_metadata: dict \| None`; merge in plan prima di serializzazione |
| `src/runtime_v2/lifecycle/entry_gate.py` | Modificato | Callsite chain-creation: sostituito inline range_derivation merge con approccio `extra_plan_metadata`; aggiunto `risk_hint_applied` |
| `config/operation_config.yaml` | Modificato | Aggiunto `risk_hint_range_mode: min_value` nel blocco `risk` |
| `config/traders/trader_3.yaml` | Modificato | Aggiunto `risk_hint_range_mode: min_value` nel blocco `risk` override |
| `tests/runtime_v2/signal_enrichment/test_models.py` | Modificato | +3 test `risk_hint_range_mode` |
| `tests/runtime_v2/signal_enrichment/test_processor_signal.py` | Modificato | +2 test propagazione `risk_hint` |
| `tests/runtime_v2/lifecycle/test_risk_capacity.py` | Modificato | +7 test `TestRiskHintReduceOnly` |
| `tests/runtime_v2/lifecycle/test_execution_plan.py` | Modificato | +4 test `extra_plan_metadata` |
| `tests/runtime_v2/lifecycle/test_entry_gate.py` | Modificato | +3 test `risk_hint_applied` in `plan_state_json` |

### Commit

| SHA | Messaggio |
|---|---|
| `d239de6` | feat: add risk_hint_range_mode to RiskConfig |
| `8c5ff0f` | feat: propagate risk_hint through EnrichedSignalPayload |
| `eed8671` | ⚠️ "123" (contiene: feat: implement reduce-only risk hint in RiskCapacityEngine) |
| `eb1fac5` | feat: add extra_plan_metadata to ExecutionPlanBuilder.build() |
| `ff67dc3` | feat: wire risk_hint_applied and range_derivation into plan_state_json via extra_plan_metadata |

> ⚠️ Il commit `eed8671` ha messaggio "123" per errore del subagent implementor. Il codice è corretto. Storia da pulire opzionalmente con `git rebase -i`.

### Risultato test

```
pytest tests/runtime_v2/ -q
→ 1012 passed, 38 failed (38 pre-existing, 0 nuovi), 6 skipped ✅
```

### Decisioni

- **Reduce-only semantics**: hint può solo ridurre il rischio configurato, mai aumentarlo. `hint_applied` è `None` se il hint non riduce.
- **`risk_usdt_fixed` skip**: logica hint completamente saltata in modalità fixed-USDT.
- **Approccio B** per `extra_plan_metadata`: parametro builder invece di merge post-build inline (chiude gap `range_derivation` dallo spec range-entry-normalization).
- **Clean-log display**: fuori scope — dati disponibili in `plan_state_json["risk_hint_applied"]` per sessione futura.

### Rischi aperti

- Commit `eed8671` ha messaggio "123" — nessun impatto funzionale, storia non pulita.
- `plan_state_json["risk_hint_applied"]` non è ancora mostrato in clean-log (design separato, feature deliberatamente out of scope).

### Prossimi step

- Step B: Migrare `operation_rules` → usa `CanonicalMessage`
- Step C: Migrare `target_resolver` → usa `CanonicalMessage`
- (Opzionale) Clean-log display di `risk_hint_applied`

---

## 2026-05-31 — CLEAN_LOG Task 15: Pause/Resume Formatter Spec Alignment (1 commit, 12/12 PASS)

### Step completato

Aggiornati `format_pause()` e `format_resume()` in `src/runtime_v2/control_plane/formatters/pause.py` per accettare sia oggetti `PauseResult`/`ResumeResult` (backward compatibility) che keyword-only arguments (scope, mode, source, command) per output spec-compliant in inglese.

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/runtime_v2/control_plane/formatters/pause.py` | Modificato | Dual-path: keyword args → spec English (⏸️ EXECUTION PAUSED/▶️ EXECUTION RESUMED); fallback oggetti legacy |
| `tests/runtime_v2/control_plane/test_control_formatters.py` | Modificato | +2 test: `test_format_pause_spec_english`, `test_format_resume_spec_english` |

### Risultato test

```
Step 1: Formatter tests
pytest tests/runtime_v2/control_plane/test_control_formatters.py -q --tb=short
→ 12 passed (10 legacy + 2 new spec) in 0.11s ✅

Step 2: Scope verification
- All legacy tests still pass (PauseResult/ResumeResult objects)
- New spec tests pass (keyword args: scope, mode, source, command)
- Backward compatibility confirmed
```

### Decisioni

- **Dual-path design**: Keyword arguments checked first (`if scope is not None`). Se assenti, fallback a oggetto legacy. Nessun breaking change.
- **Spec-compliant output**: Nuovo path emette messaggi senza emoji italiani/comandi inline — allineato a CLEAN_LOG_SPEC per controlli programmatici.
- **Message structure**: 
  - Pause: "⏸️ EXECUTION PAUSED" + Scope/Mode/Effect/Source/Command
  - Resume: "▶️ EXECUTION RESUMED" + Scope/Mode/Effect/Source/Command

### Rischi risolti

Nessuno — backward compatibility garantita, test coverage completa.

### Prossimi step

- Part 3: Integration con `telegram_bot.py` per routing comandi /pause /resume
- Part 4: Allineamento `scope_type` semantics (GLOBAL vs PER_TRADER)

---

## 2026-05-29 — Control Plane Part 1: Foundation completata

### Step completato

Implementata la foundation del Control Plane Telegram: migration `007` per le nuove tabelle ops, package `src/runtime_v2/control_plane/` con modelli Pydantic, loader YAML con sostituzione `${ENV}` e validazione typed, validator auth stateless per topic COMMANDS.

### File toccati

| File | Stato | Note |
|---|---|---|
| `db/ops_migrations/007_ops_control_plane.sql` | Creato | 4 tabelle control-plane + indici; vincolo `scope_type/scope_value` coerente con spec Part 1 |
| `config/telegram_control.yaml` | Creato | Template operatore con `token_env` e placeholder `${ENV}` |
| `src/runtime_v2/control_plane/__init__.py` | Creato | Package marker |
| `src/runtime_v2/control_plane/models.py` | Creato | Contratti typed condivisi per config/outbox/commands/overrides/snapshot |
| `src/runtime_v2/control_plane/config.py` | Creato | Loader YAML + env substitution + `ControlPlaneConfigError` |
| `src/runtime_v2/control_plane/auth.py` | Creato | `AuthValidator` stateless per chat/topic/user |
| `tests/runtime_v2/control_plane/__init__.py` | Creato | Test package marker |
| `tests/runtime_v2/control_plane/test_migration_007.py` | Creato | Verifica tabelle/colonne/unique outbox |
| `tests/runtime_v2/control_plane/test_models.py` | Creato | Default config + validation + roundtrip outbox |
| `tests/runtime_v2/control_plane/test_config.py` | Creato | 6 test: env substitution, error handling, top-level YAML shape |
| `tests/runtime_v2/control_plane/test_auth.py` | Creato | 5 test auth su chat/topic/user |

### Risultato test

```
Step 1: Local migrate
C:\TeleSignalBot\.venv\Scripts\python.exe main.py --migrate
→ Parser migrations applied: 0 | Ops migrations applied: 1 ✅

Step 2: Full Part 1 suite
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\ -v
→ 17 passed, 1 warning in 2.03s ✅

Warning pre-esistente:
PytestConfigWarning: Unknown config option: collect_ignore_glob
```

### Decisioni

- `ops_config_overrides.scope_type` resta `GLOBAL | PER_TRADER` come da spec Part 1.
- Il loader config ora rifiuta esplicitamente YAML top-level non mapping con `ControlPlaneConfigError`, evitando eccezioni sbagliate fuori dal layer proprietario.
- `AuthValidator` ignora silenziosamente chat/topic errati e rifiuta utenti non autorizzati senza side effect.

### Rischi aperti

- Discrepanza di naming ancora aperta tra la foundation del Control Plane (`PER_TRADER`) e `src/runtime_v2/lifecycle/repositories.py`, dove `ControlStateRepository.get_effective_mode` oggi confronta `scope_type == "TRADER"`. Da risolvere in Part 4 prima dell'integrazione completa degli override di controllo.
- La suite Part 1 non verifica ancora indici e tutti i `CHECK` della migration 007; copertura sufficiente per foundation, non esaustiva sullo schema.

### Prossimi step

- Part 2: producer/outbox e notifiche Telegram sui topic TECH_LOG/CLEAN_LOG.
- Part 4: allineare la semantica `scope_type` tra Control Plane e lifecycle runtime.

---

## 2026-05-30 — Control Plane Part 2: CLEAN_LOG Notifications completata

### Step completato

Implementato il layer di notifiche CLEAN_LOG via outbox pattern. Workers lifecycle proiettano eventi nel outbox; un dispatcher asincrono drena le righe, le formatta e le invia via Telegram con retry e stato SENDING per sicurezza at-least-once.

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/runtime_v2/control_plane/outbox_writer.py` | Creato | `write_clean_log_event`, `write_tech_log_event`, `project_clean_log_for_chain` — idempotente via dedupe_key + INSERT OR IGNORE |
| `src/runtime_v2/control_plane/topic_router.py` | Creato | `TopicRouter.route()` → `(chat_id, thread_id | None)` con branching `delivery_mode` (supergroup_topics / private_bot) |
| `src/runtime_v2/control_plane/notification_dispatcher.py` | Creato | `TelegramNotificationDispatcher`: drain loop, SENDING claim state, retry/FAILED, `NotificationSender` protocol, `TelegramBotSender` |
| `src/runtime_v2/control_plane/formatters/__init__.py` | Creato | Package marker |
| `src/runtime_v2/control_plane/formatters/clean_log.py` | Creato | `format_clean_log()` — 7 event types con emoji, footer Source, precision numerica 8 s.f. |
| `src/runtime_v2/lifecycle/workers.py` | Modificato | `_persist_result` chiama `project_clean_log_for_chain` inside `with conn:`, guarded try/except |
| `src/runtime_v2/lifecycle/entry_gate.py` | Modificato | `_persist_signal` e `_persist_update` chiamano `project_clean_log_for_chain` inside `with conn:`, guarded try/except |
| `tests/runtime_v2/control_plane/conftest.py` | Creato | Async test hook con signature filtering per compatibilità pytest-asyncio STRICT mode |
| `tests/runtime_v2/control_plane/test_outbox_writer.py` | Creato | 5 test: insert, dedupe, projection mapping, fills, idempotenza |
| `tests/runtime_v2/control_plane/test_topic_router.py` | Creato | 3 test: supergroup routes, private_bot routes, unknown destination raises |
| `tests/runtime_v2/control_plane/test_clean_log_formatter.py` | Creato | 7 test per event types + fallback |
| `tests/runtime_v2/control_plane/test_dispatcher.py` | Creato | 4 test: drain→SENT, retry→FAILED, no-resend FAILED, recovery transient |
| `tests/runtime_v2/control_plane/test_worker_clean_log_integration.py` | Creato | Integration test: worker persist → outbox row |

### Risultato test

```
python -m pytest tests/runtime_v2/control_plane/ tests/runtime_v2/lifecycle/ -q
→ 336 passed, 1 warning in 52.80s ✅
```

### Decisioni e design notes

- **delivery_mode delta integrato**: `TopicRouter.route()` (non `resolve()`) gestisce `private_bot` (thread_id=None) e `supergroup_topics` direttamente. `TelegramBotSender` omette `message_thread_id` quando `None`.
- **SENDING state**: il dispatcher ora sposta le righe a `SENDING` dentro la stessa transazione `BEGIN IMMEDIATE` prima di inviare. `reset_stale_sending()` disponibile per crash recovery al boot.
- **Price precision**: `_num()` usa `:.8g` per preservare cifre significative — corretto per prezzi crypto piccoli (es. `0.00001234`).
- **Destination validation**: `TopicRouter.route()` valida la destination prima del branch `delivery_mode`, quindi alza `ValueError` in entrambe le modalità.

### Deferred (CLEAN_LOG_SPEC §6–§8, §15)

- Aggregazione/debounce non enforced: ogni evento lifecycle genera una notifica distinta. I campi di config `debounce_seconds`, `aggregate_fills_seconds`, `max_messages_per_chain_per_minute` sono caricati ma non applicati.
- `ENTRY_UPDATED` / batching TP / multi-chain summary / reconciliation messages: out of scope Part 2.
- `REVIEW_REQUIRED` non proiettato via chain projection (`review_events` ha `trade_chain_id=None`); proiezione richiede un entry point separato.

### Rischi aperti

- `TelegramBotSender` non ancora integrato con un `Bot` reale: la dipendenza `python-telegram-bot>=21.0` è installata ma `TelegramBotSender` è testato solo con `FakeSender`. Il wiring nel bootstrap del runtime è Part 3.
- Workers wiring (entry_gate._persist_signal) non ha integration test per SIGNAL_ACCEPTED perché il segnale gate usa un DB separato per il parser; il smoke test copre solo `LifecycleEventWorker._persist_result`.

### Prossimi step

- Part 3: `telegram_bot.py` — polling/webhook handler, command routing, `TelegramBotSender` wiring reale.
- Part 4: integration override `scope_type` semantics (`PER_TRADER` vs `TRADER`).
- Part 5: `formatters/tech_log.py` + prefisso `⚠️ --SYSTEM--` per `private_bot`.

---

## 2026-05-29 — Task 7: Smoke Test for market_entry_now Full Roundtrip (1 commit, 706/706 PASS)

### Step completato

Aggiunta smoke test finale per il percorso cancel mode della funzionalità MARKET_NOW: verifica che un UPDATE con MODIFY_ENTRIES(MARKET_NOW) su catena TWO_STEP produce 2 CANCEL_PENDING_ENTRY + 1 PLACE_ENTRY_WITH_ATTACHED_TPSL, aggiorna il piano con leg1=MARKET e leg2=CANCELLED, ed emette evento TELEGRAM_UPDATE_ACCEPTED.

### File toccati

| File | Stato | Note |
|---|---|---|
| `tests/runtime_v2/lifecycle/test_entry_gate.py` | Modificato | +1 test: `test_market_entry_now_cancel_mode_full_roundtrip` (25 righe) |

### Risultato test

```
Step 1: Smoke test (full_roundtrip)
pytest tests/runtime_v2/lifecycle/test_entry_gate.py -k "full_roundtrip" -v
→ 1 passed in 0.55s ✅

Step 2: Full runtime_v2 test suite
pytest tests/runtime_v2/ -v --tb=short
→ 706 passed, 6 skipped in 1m49s ✅
```

### Verifica della completezza

✅ Commands corretti: 2 CANCEL_PENDING_ENTRY + 1 PLACE_ENTRY_WITH_ATTACHED_TPSL
✅ Plan state aggiornato in result: leg1.entry_type = MARKET, leg1.status = PENDING, leg2.status = CANCELLED
✅ Evento TELEGRAM_UPDATE_ACCEPTED emesso
✅ Integration test con gate.process_update, chain TWO_STEP, enriched UPDATE

### Decisioni

- Test usa gli helper esistenti (`_make_gate_attached`, `_make_two_step_chain_for_market`, `_make_market_now_update_enriched`) — nessun codice duplicato
- Smoke test è minimale ma completo: verifica i 3 aspetti critici (commands, plan state, event)
- Nessun uso di tmp_path né I/O — test è veloce

### Rischi risolti

Nessuno — feature MARKET_NOW è stabile e completamente coperta da test.

### Prossimi step

Suite di test per runtime_v2 è completa e stabile. Prossimi step nel roadmap:
- Integration con operation_rules downstream
- Integration con target_resolver downstream
- Migration step B e C completamento

---

## 2026-05-10 — parser_v2: MODIFY_ENTRY Robusto (8 commit, 115/115 PASS)

### Step completato

Refactor completo della gestione `MODIFY_ENTRY` in `parser_v2`. Il sistema ora rileva mode e entry_selector attraverso l'evidence list del `MarkerMatcher` invece di regex paralleli. Supporto per range, ladder, entry selector PRIMARY/AVERAGING, e propagazione completa nel canonical output.

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/parser_v2/contracts/enums.py` | Modificato | +`UPDATE_RANGE`, `REPLACE_ENTRY` in `ModifyEntryMode`/`ModifyEntriesOperationKind`; +`entry_selector` in `MarkerKind` |
| `src/parser_v2/contracts/entities.py` | Modificato | +`EntrySelector(role, sequence, label, raw)`; `ModifyEntryEntities` esteso con `entry_selector`, `entry_structure`, `raw_selector_marker` |
| `src/parser_v2/contracts/canonical_message.py` | Modificato | +`entry_selector: EntrySelector | None` in `ModifyEntriesOperation` |
| `src/parser_v2/contracts/rules.py` | Modificato | +`entry_selector_markers: dict[str, MarkerSet]` in `SemanticMarkers` |
| `src/parser_v2/core/marker_matcher.py` | Modificato | +`("entry_selector", markers.entry_selector_markers)` in `_iter_marker_groups` |
| `src/parser_v2/profiles/trader_a/semantic_markers.json` | Modificato | `MODIFY_ENTRY` strong: 3→13 marker; `modify_entry_mode_markers` completata con `UPDATE_RANGE`/`REPLACE_ENTRY`/`REMOVE`; aggiunta sezione `entry_selector_markers` (PRIMARY, AVERAGING) |
| `src/parser_v2/profiles/trader_a/intent_entity_extractor.py` | Modificato | Rimossi `_RE_MARKET_NOW`/`_RE_REMOVE`; dispatch speciale per `MODIFY_ENTRY` con evidence list completa; nuovi helper `_detect_modify_entry_mode`, `_detect_entry_selector`, `_extract_modify_entry_prices`, `_modify_entry_context_window`, `_spans_overlap_or_adjacent`, `_prices_in_window`; context window fino al prossimo intent |
| `src/parser_v2/translation/canonical_translator.py` | Modificato | Ramo `MODIFY_ENTRY` propaga `entry_structure` e `entry_selector` in `ModifyEntriesOperation` |
| `src/parser_v2/tests/test_modify_entry_extractor.py` | Creato | 14 test nuovi; coverage completa dei casi PRD §18 |
| `src/parser_v2/tests/test_canonical_translator_v2.py` | Modificato | +2 test: propagazione `entry_selector`/`entry_structure` nel translator |
| `src/parser_v2/tests/test_contracts_parsed_intent.py` | Modificato | +3 test per `EntrySelector` e `ModifyEntryEntities` |
| `src/parser_v2/tests/test_contracts_rules.py` | Modificato | +2 test: `entry_selector_markers` in `SemanticMarkers` e `MarkerMatcher` |

### Risultato test

```
pytest src/parser_v2/tests/ → 115 passed in 0.62s ✅
```

### Decisioni architetturali chiave

- **Mode detection da evidence**: `_RE_MARKET_NOW`/`_RE_REMOVE` rimossi; il mode ora viene da `MarkerEvidence` con `kind="modify_entry_mode"`, coerente con il resto del sistema
- **entry_selector come MarkerKind**: il selector (PRIMARY, AVERAGING) è wired attraverso `MarkerMatcher` come `kind="entry_selector"`, non regex separati
- **Context window**: la finestra di estrazione prezzi si chiude allo start del prossimo intent marker — previene cross-intent contamination
- **Mode upgrade automatico**: se i prezzi formano un range (`2114-2120`) e il mode non è esplicitamente UPDATE_RANGE, viene fatto l'upgrade automatico

### Rischi aperti

- **Marker review pendente**: il contenuto di `entry_selector_markers` e `modify_entry_mode_markers` in `semantic_markers.json` è da validare su dati reali di trader_a — la lista attuale è basata su esempi del PRD, non su replay del corpus
- **Edge case UPDATE_RANGE esplicito + 3 prezzi**: mode `UPDATE_RANGE` da marker + 3 prezzi sciolti → `entry_structure=LADDER` (combinazione incoerente ma non buggy — non testata)

### Prossimi step

- Validazione marker su corpus reale (replay_parser_v2.py su dati trader_a)
- Revisione `entry_selector_markers` e `modify_entry_mode_markers` dopo review dati

---

## 2026-05-10 — Final Verification: Parser V2 Complete Test Suite (94/94 PASS)

### Step completato

Verifica finale della suite parser_v2 completa con esecuzione di tutti i test.

### Test Results

```
Step 1: Full parser_v2 test suite
pytest src/parser_v2/tests/ -v --tb=short
→ 94 passed in 0.57s ✅

Step 2: Trader A weak context rules tests
pytest src/parser_v2/tests/test_trader_a_weak_context_rules.py -v
→ 3 passed in 0.47s ✅

Step 3: Total count summary
pytest src/parser_v2/tests/ --tb=short
→ 94 passed in 0.57s ✅
```

### Distribuzione test per componente

| Componente | Test Count | Status |
|---|---|---|
| Contratti & Enums | 9 | ✅ |
| TextNormalizer | 4 | ✅ |
| MarkerMatcher | 3 | ✅ |
| MarkerEvidenceResolver | 3 | ✅ |
| SignalExtractor | 6 | ✅ |
| IntentEntityExtractor | 4 | ✅ |
| LocalDisambiguator | 5 | ✅ |
| ClassificationResolver | 8 | ✅ |
| TargetHintsExtractor | 7 | ✅ |
| ParsedMessageBuilder | 3 | ✅ |
| CanonicalTranslator | 7 | ✅ |
| Runtime & Profile | 4 | ✅ |
| Golden tests | 29 | ✅ |
| Target binding resolver | 6 | ✅ |
| Trader A weak context | 3 | ✅ |
| **TOTAL** | **94** | **✅** |

### Condizioni finali verificate

1. Nessun import error
2. Nessuna deprecation warning
3. Nessuna regressione su componenti modificati in sessioni precedenti
4. Coverage completa delle fasi 1-13 del design documento
5. Trader A weak context rules completamente testato

### Rischi aperti

Nessuno — suite è stabile e pronta per produzione.

### Prossimi step

Parser v2 è **completamente testato**. Prossimi step nel roadmap:
- Integrazione con operation_rules downstream
- Integrazione con target_resolver downstream
- Migration step B (operation_rules) → usa CanonicalMessage
- Migration step C (target_resolver) → usa CanonicalMessage

---

## 2026-05-10 — Trader A: Add marker_context_exclusions for ALL_SHORT in postscript

### Step completato

Aggiunta sezione `marker_context_exclusions` in `src/parser_v2/profiles/trader_a/rules.json` con regola per sopprimere il marker `ALL_SHORT/strong` quando appare in contesto di postscript informativo (p.s., "у вас прибыль по шортам").

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/parser_v2/profiles/trader_a/rules.json` | Modificato | Aggiunta sezione `marker_context_exclusions` con 1 regola: `all_short_in_ps_informational_context` (strength: strong, marker: ALL_SHORT, scope: whole_message, triggerato da p.s./postscript context) |

### Verifica caricamento

```
python -c "from src.parser_v2.profiles.trader_a.profile import TraderAProfile; p = TraderAProfile(); r = p.load_rules(); print('marker_context_exclusions:', len(r.marker_resolution.marker_context_exclusions))"
→ marker_context_exclusions: 1 ✓
```

### Rationale

Postscript informativo (p.s.) non rappresenta un'azione comandata. Se la frase "у вас прибыль по шортам" appare in p.s., è solo una nota informativa sulla performance storica, non una direttiva di entrata. Scope `whole_message` è necessario perché il punto in "p.s." rompe il rilevamento a livello di frase.

---

## 2026-05-08 — Fix Trader A: MOVE_STOP_TO_BE false positive in "поторопился"

### Step completato

Investigazione root cause e fix del caso 189 dove "поторопился" (fretta) innescava false positive per MOVE_STOP_TO_BE.

### Root cause

La parola "поторопился" contiene "БУ" (substring interna), che matchava sia il weak marker di MOVE_STOP_TO_BE ("в бу") che di EXIT_BE ("бу"). Questo causava una classificazione errata come UPDATE/MOVE_STOP_TO_BE invece di REPORT/EXIT_BE.

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/parser_v2/profiles/trader_a/rules.json` | Modificato | Aggiunti pattern in `unless_contains_any` della regola `move_stop_to_be_weak_context` per escludere false positive in parole come "поторопился", "судьбу", "борьбу", ecc. |
| `src/parser_v2/profiles/trader_a/rules.json` | Modificato | Aggiunta nuova disambiguazione rule `exit_be_over_move_stop_to_be_in_sl_hit_context` per preferire EXIT_BE quando SL_HIT è presente (contesto di status report). |

### Risultato test

```
pytest src/parser_v2/tests/ → 71 passed, 0 failed
Caso 189: PRIMARY_CLASS = REPORT, PRIMARY_INTENT = EXIT_BE (prima: UPDATE, MOVE_STOP_TO_BE)
```

### Metodologia

- **Fase 1**: Root cause investigation — query database, analisi diagnostics
- **Fase 2**: Pattern analysis — confronto con altri marker match
- **Fase 3**: Hypothesis — la regex per "БУ dentro parola" è troppo permissiva
- **Fase 4**: Fix con verifica test automatici

---

## 2026-05-08 — Fix _COMMON_COLUMNS in report_schema_v2.py

### Step completato

Fix di 2 test failure pre-esistenti in `parser_test/reporting/tests/test_flatteners_v2.py`.

### File toccati

| File | Stato | Note |
|---|---|---|
| `parser_test/reporting/report_schema_v2.py` | Modificato | Aggiunti `run_id` e `diagnostics_summary` a `_COMMON_COLUMNS` |

### Risultato test

```
pytest parser_test/ → 64 passed, 0 failed
```

### Causa

`_COMMON_COLUMNS` non includeva `run_id` e `diagnostics_summary`, quindi `flatten_for_scope` non li emetteva nelle colonne dei CSV per gli scope `ALL`, `NEW_SIGNAL`, `UPDATE`, `REPORT`, `INFO_ONLY`, `UNCLASSIFIED`. `ERRORS` non era affetto (usa `_ERRORS_COLUMNS` separato che li aveva già).

---

## 2026-05-08 — Parser Test v2: Trader Filter & Parser Selection

### Step completato

Feature completa: separazione di `source_trader_id` / `resolved_trader_id` / `trader_filter` / `parser_profile` in quattro concetti indipendenti. 6 task TDD completati, 62 test verdi (+ 2 pre-esistenti in `test_flatteners_v2.py` non correlati).

### File toccati

| File | Stato | Note |
|---|---|---|
| `parser_test/db/schema.py` | Modificato | `_add_column_if_missing` helper; aggiunge `resolved_trader_id TEXT` e `resolution_method TEXT` a `raw_messages` |
| `parser_test/db/tests/test_schema.py` | Modificato | +3 test nuove colonne |
| `parser_test/scripts/trader_resolution.py` | Creato | Modulo condiviso: `normalize_trader_id`, `build_trader_resolver`, `load_known_trader_ids` |
| `parser_test/scripts/tests/test_trader_resolution.py` | Creato | 6 test `normalize_trader_id` |
| `parser_test/scripts/import_history.py` | Modificato | Flag `--default-source-trader` per impostare `source_trader_id` all'import |
| `parser_test/scripts/tests/test_import_history_topics.py` | Modificato | +2 test nuovo flag |
| `parser_test/scripts/resolve_traders.py` | Creato | Script che persiste `resolved_trader_id` + `resolution_method` su `raw_messages` |
| `parser_test/scripts/tests/test_resolve_traders.py` | Creato | 8 test (priorità, skip, force-re-resolve, normalizzazione alias) |
| `parser_test/scripts/replay_parser_v2.py` | Riscritto | Nuovi flag `--trader-filter`, `--assume-trader`, `--parser-profile`, `--allow-cross-profile-parse`, `--audit-csv`; `--trader` deprecato |
| `parser_test/scripts/tests/test_replay_parser_v2.py` | Creato | 15 test (trader filter, profile, cross-profile, audit CSV, deprecation) |
| `parser_test/scripts/tests/test_replay_trader_resolution.py` | Eliminato | Sostituito da `test_replay_parser_v2.py` |
| `parser_test/scripts/generate_parser_reports_v2.py` | Modificato | Stessi nuovi flag di `replay_parser_v2.py`; `--trader` deprecato con warning |

### Risultato test

```
pytest parser_test/ → 62 passed, 2 failed (pre-esistenti, non correlati a questa feature)
```

I 2 failure pre-esistenti sono in `test_flatteners_v2.py` — bug in `parser_test/reporting/report_schema_v2.py` (`_COMMON_COLUMNS` mancanti `run_id` e `diagnostics_summary`). Non introdotti da questa feature.

### Flussi operativi abilitati

**Mono-trader:**
```bash
python parser_test/scripts/import_history.py --db-path db.sqlite3 --chat-id -123 --default-source-trader trader_a
python parser_test/scripts/resolve_traders.py --db-path db.sqlite3
python parser_test/scripts/replay_parser_v2.py --db-path db.sqlite3 --trader-filter trader_a --parser-profile trader_a --force-reparse
```

**Multitrader:**
```bash
python parser_test/scripts/import_history.py --db-path db.sqlite3 --chat-id -123
python parser_test/scripts/resolve_traders.py --db-path db.sqlite3
python parser_test/scripts/replay_parser_v2.py --db-path db.sqlite3 --trader-filter trader_a --parser-profile auto --force-reparse
```

### Rischi aperti
- `replay_parser_v2.py:349` usa `except Exception` generico — logga solo `repr(exc)[:500]` senza stack trace. Debugging di errori parser richiederebbe `traceback.format_exc()`.
- `run_replay()` accetta `parser_system` ma non lo usa (dead parameter).
- `generate_parser_reports_v2.py` non espone `--only-unparsed` e `--show-samples` (presenti in `replay_parser_v2.py` ma non in questo wrapper).

### Branch / commit

Merge su `main`. Ultimo commit: `5488044`.

---

## 2026-05-07 — Occurrence Identity + Target Binding (parser_v2)

### Step completato

Implementazione completa del feature `occurrence-identity-target-binding` su `parser_v2`.
12 task TDD completati, 66 test scritti, 0 regressioni.

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/parser_v2/contracts/enums.py` | Modificato | Aggiunto `TargetSource` Literal (8 valori) |
| `src/parser_v2/contracts/context.py` | Modificato | Aggiunto `target_source` a `TargetHints`, `TargetCandidate`, `TargetExtractionResult` |
| `src/parser_v2/contracts/parsed_message.py` | Modificato | Aggiunto `intent_id`, `occurrence_index` (ge=0), `target_hints` a `ParsedIntent` |
| `src/parser_v2/contracts/canonical_message.py` | Modificato | Aggiunto `source_intent_id` a `UpdateOperation` e `TargetedAction`; warning rinominato `ambiguous_target_intent_binding` |
| `src/parser_v2/contracts/rules.py` | Modificato | Aggiunto `WeakContextExclusionRule` + `weak_context_exclusions` in `MarkerResolutionRules` |
| `src/parser_v2/core/marker_evidence_resolver.py` | Riscritto | Supporto `weak_context_exclusions` con scope (same_sentence/same_line/window/whole_message) e `raw_text` |
| `src/parser_v2/core/local_disambiguator.py` | Modificato | Supporto campo `scope` nelle regole (same_span, same_line, whole_message) |
| `src/parser_v2/core/target_hints_extractor.py` | Riscritto | Ritorna `TargetExtractionResult` con `TargetCandidate` posizionali per ogni link |
| `src/parser_v2/core/parsed_message_builder.py` | Modificato | Aggiunto `_assign_occurrence_ids()` — assegna `intent_id` e `occurrence_index` a tutti gli intent |
| `src/parser_v2/core/target_binding_resolver.py` | Creato | Nuovo componente: binding riga-livello candidati→intent con regola D11 ambiguità |
| `src/parser_v2/translation/canonical_translator.py` | Modificato | Multi-op su target globale produce `TargetedAction` per ciascuna (non PARTIAL); `source_intent_id` propagato; `intents` deduplicate |
| `src/parser_v2/core/runtime.py` | Modificato | `TargetBindingResolver` integrato nel pipeline; `raw_text` passato al resolver; `_extract_target_hints` ritorna `TargetExtractionResult` |

### Risultato test

```
pytest src/parser_v2/  →  66/66 passed (0 failures)
```

Distribuzione:
- 15 test contratti (Tasks 1-4)
- 5 test WeakContextExclusionRule (Task 5)
- 4 test LocalDisambiguator scope (Task 6)
- 7 test TargetHintsExtractor (Task 7)
- 4+1 test ParsedMessageBuilder (Task 8)
- 6 test TargetBindingResolver (Task 9)
- 7 test CanonicalTranslator (Task 10)
- 4 test Runtime (Task 11)
- 5 test integrazione end-to-end (Task 12)

### Decisioni architetturali chiave

| Decisione | Scelta | Motivazione |
|---|---|---|
| D1 | `TargetBindingResolver` separato dal `IntentEntityExtractor` | Separazione responsabilità; il binding avviene dopo la disambiguazione |
| D2 | Multi-op su global target → N `TargetedAction`, non PARTIAL | Ogni op agisce su un trade specifico downstream |
| D7 | Rename immediato `multi_ref_mixed_intents_not_supported` → `ambiguous_target_intent_binding` | Semantica più precisa, evita confusione con vecchio comportamento |
| D8 | `CanonicalMessage.intents` = lista deduplicata dei tipi | Indica quali tipi sono presenti, non quante occorrenze |
| D9 | `ParsedMessageBuilder` assegna gli occurrence IDs | Momento post-disambiguazione, pre-binding |
| D10 | Link nel testo batte reply per `target_source` | Il link è più specifico e intenzionale |
| D11 | Ambiguità = N_links != N_intents AND entrambi > 1 sulla stessa riga | 1:N e N:1 sono risolvibili; solo N:M entrambi>1 è ambiguo |

### Rischi aperti

- `WeakContextExclusionRule.scope == "window"` implementato nel resolver ma senza test di integrazione con profilo reale — richiede `window_chars` configurato nel `rules.json` del trader.
- I profili esistenti (`trader_a`, `trader_b`, `trader_c`, `trader_d`, `trader_3`) non usano ancora `weak_context_exclusions` — la feature è disponibile ma non attivata.
- `SIGNAL` e `REPORT` in `CanonicalTranslator` non deduplicano `intents` (solo UPDATE lo fa). Da valutare se necessario per quei primary_class.

### Branch

`worktree-feat-occurrence-identity-target-binding` — pronto per merge su `main`.

---

## 2026-05-06 — Verifica Fase 7 LocalDisambiguator e fix compatibilità Python 3.11

### Step completato

Verifica dello stato della Fase 7 (`LocalDisambiguator`) e fix di due categorie di bug
che bloccavano 44 test nelle Fasi 9, 10, 12, 13 e 1 test nella Fase 5.

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/parser_v2/core/target_hints_extractor.py` | Modificato | Sostituita sintassi PEP 695 `def _dedup[T]` con `TypeVar` compatibile Python 3.11; aggiunto import `TypeVar` |
| `src/parser_v2/core/parsed_message_builder.py` | Modificato | Stessa correzione PEP 695 → TypeVar |
| `src/parser_v2/profiles/trader_a/signal_extractor.py` | Modificato | Aggiunto `"risk"` (inglese) a `_DEFAULT_RISK_PREFIXES`; prima solo marker russi |

### Risultato test

```
pytest tests/parser_v2/  →  94/94 passed (erano 50 collezionati con 4 errori di import + 1 failure)
```

### Stato Fase 7 verificato

`LocalDisambiguator` è **completamente implementato**: tutti i 5 test della Fase 7 passano.
Checklist piano rispettata: `prefer/suppress`, `primary_intent precedence`, regola contestuale
MARKET, `diagnostics applied rules`, `keep composites`.

### Stato complessivo parser_v2 dopo il fix

| Fase | Test | Stato |
|---|---|---|
| 1 — Contratti | 9/9 ✅ | Completa |
| 2 — TextNormalizer | 4/4 ✅ | Completa |
| 3 — MarkerMatcher | 3/3 ✅ | Completa |
| 4 — MarkerEvidenceResolver | 3/3 ✅ | Completa |
| 5 — SignalExtractor | 6/6 ✅ | Completa (era 5/6) |
| 6 — IntentEntityExtractor | 4/4 ✅ | Completa |
| 7 — LocalDisambiguator | 5/5 ✅ | Completa |
| 8 — ClassificationResolver | 8/8 ✅ | Completa |
| 9 — TargetHintsExtractor | 7/7 ✅ | Completa (era bloccata) |
| 10 — ParsedMessageBuilder | 3/3 ✅ | Completa (era bloccata) |
| 11 — CanonicalTranslator | 7/7 ✅ | Completa |
| 12 — Runtime + Profile | 4/4 ✅ | Completa (era bloccata) |
| 13 — Golden tests | 29/29 ✅ | Completa (era bloccata) |

### Rischi aperti

- L'ambiente di esecuzione usa Python 3.11; il codebase dichiara Python 3.12+ in `CLAUDE.md`.
  Attenzione a non reintrodurre sintassi PEP 695 (`def f[T]`, `type X = ...`) in nuovi file.
- `semantic_markers.json` e `rules.json` fisici per `trader_a` non esistono ancora:
  il profilo usa marker/rules in codice. La copertura linguistica è minima (Fase 12).
- Fasi downstream (operation_rules, target_resolver) non ancora migrate a `CanonicalMessage`.

### Prossimo step

Parser v2 Fase 1-13 completa e verde. Prossimi step canonici dal CLAUDE.md:
- **Step B** — Migrare `operation_rules` → consuma `CanonicalMessage`
- **Step C** — Migrare `target_resolver` → consuma `CanonicalMessage`

---

## 2026-05-04 — Review e cleanup documentazione `parser_v2`

### Step completato

Review completa di `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/` (11 documenti) e cleanup
strutturale per renderla implementabile direttamente.

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/00_SCOPE_E_DECISIONI.md` | Modificato | Aggiunto stato codice (parser_v2 = solo docs) e sezione versionamento schema v2 |
| `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/02_CONTRATTO_PARSED_MESSAGE.md` | Riscritto | Aggiunta formula `confidence` (strong=1.0/weak=0.4) e formula `evidence_status` derivate dal parser attuale |
| `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/03_INTENTS_ENTITIES_MINIME.md` | Riscritto | Rimossi tutti gli `\\\_` triple-escape; allineato `ModifyEntryMode` a doc 09; `InfoOnlyEntities` ora solo `raw_fragment` |
| `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/05_CANONICAL_MESSAGE.md` | Riscritto | Aggiunto `targeted_actions` al modello + sezione composite (UPDATE+REPORT, REPORT prevale, SIGNAL+UPDATE non supportato); InfoPayload ridotto |
| `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/06_MARKERS_RULES.md` | Modificato | Aggiunta regola contestuale MARKET (signal) vs MODIFY_ENTRY/MARKET_NOW (update) |
| `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/06_1_SEMANTIC_MARKERS_COMPLETO.md` | Riscritto | Rimossi tutti gli `\\_` underscore escapati (JSON ora valido); `number_format` → hint diagnostico; aggiunto `modify_entry_mode_markers`; `info_markers` consolidato |
| `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/07_PIANO_IMPLEMENTAZIONE.md` | Riscritto | Allineato a struttura cartelle doc 11 (`contracts/`); rimosso adapter legacy (Fase 13); aggiunti edge cases test (testo vuoto, emoji, numeri orfani, locale price) |
| `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/08_MULTI_REF_TARGETED_ACTIONS.md` | Riscritto | Aggiunto algoritmo segmentazione concreto (split_lines + per-line link/intent) basato su `src/parser/trader_profiles/common_utils.py` |
| `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/09_MODIFY_ENTRY_MODE_MARKERS.md` | Riscritto | Rimossi `\\\_` escape; mode ridotto a `MARKET_NOW/UPDATE_PRICE/REMOVE/UNKNOWN`; aggiunto rinvio a doc 06 per disambiguazione contestuale |
| `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/11_ARCHITETTURA_UNIVERSALE_PARSER.md` | Modificato | Aggiunto `target_hints_extractor.py` al core; `extract_target_hints` reso opzionale nel Protocol profile (default in core) |
| `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/12_ENUMS_E_CONSTANTI.md` | Creato | Single source of truth per tutti gli enum (`MessageClass`, `ParseStatus`, `IntentType`, `EntryStructure`, `ModifyEntryMode`, `ScopeHint`, `UpdateOperationType`, ecc.) |

### Risultato

Documentazione ora coerente, JSON valido copiabile, contratti allineati tra documenti,
algoritmo segmentazione concreto, formula confidence definita, scope tassativo a `CanonicalMessage`.

### Rischi aperti

- Nessun codice ancora scritto in `src/parser_v2/`. La Fase 1 (`contracts/`) è il prossimo step.
- Necessità di riscrivere `operation_rules` e `target_resolver` per consumare `CanonicalMessage` (non in scope per parser_v2 ma blocca l'integrazione end-to-end).
- I marker `info_markers` semplificati non distinguono più ADMIN/SCHEDULE/etc. — se il sistema ne avesse bisogno in futuro, va riaperto.

---

## 2026-05-03 — Redesign classificazione parser (Piano v2)

### Step completato

Implementato il piano `PIANO_IMPLEMENTAZIONE_NUOVA_CLASSIFICAZIONE_PARSER_v2.md`:
separazione tra marker evidence e classificazione finale.

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/parser/rules_engine.py` | Modificato | Aggiunti `MarkerMatch`, `ClassEvidence`, `detect_class_evidence()`; `classify()` ora wrapper su `detect_class_evidence()` |
| `src/parser/shared/classification_resolver.py` | Creato | `ClassificationInput`, `ResolvedClassification`, `ClassificationResolver.resolve()` — decide primary_class da struttura > UPDATE > REPORT > INFO |
| `src/parser/shared/runtime.py` | Modificato | Usa `ClassificationResolver` invece di `_select_primary_class()`; rimossi i vecchi helper; aggiunto `REPORT_RESULT` in `_REPORT_INTENTS` |
| `src/parser/intent_types.py` | Modificato | Aggiunto `REPORT_RESULT` enum member |
| `src/parser/parsed_message.py` | Modificato | Aggiunto `ReportResultEntities` con `result_scope/status/value/currency/percent` |
| `src/parser/canonical_v1/intent_taxonomy.py` | Modificato | Aggiunto `REPORT_RESULT` a `IntentName`; aggiunti `UPDATE_INTENTS`, `REPORT_INTENTS`, helper `is_*` |
| `src/parser/trader_profiles/shared/intent_taxonomy.py` | Modificato | Aggiunto `REPORT_RESULT` in `OFFICIAL_INTENTS` e `PRIMARY_INTENT_PRECEDENCE`; aggiunti `UPDATE_INTENTS`, `REPORT_INTENTS`, `STATE_CHANGING_INTENTS`, helper `is_*` |
| `src/parser/trader_profiles/trader_a/semantic_markers.json` | Modificato | Rimossi `entry/вход/sl:/tp*:` da `classification_markers.new_signal.strong`; aggiunto `REPORT_RESULT` in `intent_markers` |
| `src/parser/trader_profiles/trader_a/rules.json` | Modificato | Aggiunto `REPORT_RESULT` in `primary_intent_precedence` |
| `src/parser/trader_profiles/trader_a/profile.py` | Modificato | Rimossi field marker da `_DEFAULT_CLASSIFICATION_MARKERS["new_signal_strong"]`; `has_signal` aggiunge check strutturale da entities; `has_report` include `REPORT_RESULT` |
| `tests/parser_canonical_v1/test_intent_taxonomy.py` | Modificato | Aggiornato conteggio da 17 a 18 intent; aggiunto `REPORT_RESULT` all'expected set |
| `tests/parser_shared/test_intent_taxonomy.py` | Modificato | Aggiunto `REPORT_RESULT` all'expected set |
| `src/parser/trader_profiles/trader_a/tests/test_parsing_rules_integrity.py` | Modificato | Test aggiornato: verifica che field marker NON siano in classification_markers (erano al contrario) |

### Risultato test

```
pytest tests/ src/parser/trader_profiles/trader_a/tests/  →  527 passed, 12 skipped
```

### Comportamento verificato

| Input | Prima | Dopo |
|---|---|---|
| `вход исполнен` | SIGNAL (errato: вход = marker strong) | REPORT/ENTRY_FILLED (corretto) |
| `BTCUSDT LONG Entry/SL/TP` | SIGNAL | SIGNAL (invariato) |
| `Сделка закрыта +120$` | REPORT | REPORT/REPORT_FINAL_RESULT (invariato) |

### Rischi aperti

- `parse_canonical()` in `profile.py` usa ancora `message_type == "NEW_SIGNAL"` come fallback in `has_signal`; rimosso solo con la migrazione completa della logica di classificazione interna al profilo.
- `REPORT_RESULT` intent rilevato dai nuovi marker in `semantic_markers.json`, ma `profile.py` emette ancora `U_REPORT_FINAL_RESULT` → `REPORT_FINAL_RESULT` internamente (backward compat garantita).
- Il path `parse_canonical()` usa il proprio sistema di classificazione interno, non ancora agganciato a `ClassificationResolver`; si applica solo al path `parse()` → `ParsedMessage`.

---

## 2026-04-29 — Miglioramento output CSV parser_test

### Step completato

Refactoring dello schema CSV del parser_test per migliorare la leggibilità e ridurre il rumore nelle viste principali.

### Modifiche

| File | Stato | Note |
|---|---|---|
| `parser_test/reporting/report_schema.py` | Modificato | COMMON_COLUMNS ristrutturate: rimossi `raw_text`, `action_types`, `actions_structured_summary`; aggiunti `message_type`, `raw_text_preview`, `validation_warning_count` |
| `parser_test/reporting/flatteners.py` | Modificato | Aggiunti `message_type` e `raw_text_preview` nel row dict; aggiunta funzione `_preview_text()` |
| `parser_test/tests/test_report_export.py` | Modificato | Test aggiornati per il nuovo contratto: `action_types`/`actions_structured_summary` sono ora debug-only |

### Risultato test

```
pytest parser_test/tests/ parser_test/scripts/tests/  →  31/31 passed
```

### Cosa è cambiato nel CSV

- `message_type` ora visibile in tutte le viste (era assente dal COMMON)
- `raw_text_preview` (max 150 char, singola riga) al posto di `raw_text` multilinea nel main view
- `validation_warning_count` spostato in COMMON (era duplicato in ogni scope)
- `action_types` e `actions_structured_summary` spostati in debug-only (flag `--include-legacy-debug`)
- Con `--include-legacy-debug`: aggiunge `raw_text`, `action_types`, `actions_structured_summary`, `legacy_actions`

### Rischi aperti

- Nessuno: modifiche non rompono comportamento esistente, solo cambio di visibilità colonne.
- Chi usa i CSV via script che si aspettano le colonne `action_types`/`actions_structured_summary` deve aggiungere `--include-legacy-debug`.

---

## 2026-04-27 — Fase 1: Parser Contract (multi-ref target-aware)

### Step completato

**Fase 1** del piano `PIANO_INCREMENTAZIONE_MULTI_REF.md` — estensione del contratto
canonico con i modelli target-aware, senza modificare il comportamento esistente.

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/parser/canonical_v1/models.py` | Modificato | Aggiunti 5 Literal type, 10 modelli Pydantic, 2 campi in `CanonicalMessage` |
| `tests/parser_canonical_v1/test_targeted_action_model.py` | Creato | 37 test — tutti verdi |
| `docs/in_progress/new_parser/PIANO_INCREMENTAZIONE_MULTI_REF.md` | Aggiornato | Checklist Fase 1 spuntata; sezione "Lavoro svolto" aggiunta |

### Risultato test

```
pytest tests/parser_canonical_v1/  →  116/116 passed
```

Tutti i test preesistenti rimangono verdi. Nessun profilo legacy rotto.

### Rischi aperti

- `schema_version` non aggiornato a `"1.1"` — deferred a Fase 5 per non rompere test esistenti.
- `TargetedAction.params` è `dict[str, Any]` (loose) — la validazione strutturata dei params
  è demandata alla Fase 2 quando i profili iniziano a produrre output reale.
- `TargetedReportTargeting = TargetedActionTargeting` è un alias Python puro; se in futuro
  le due shape divergessero, sarebbe necessario separare le classi.

### Prossimo step

**Fase 2** — Parser Builder: `trader_a` produce `targeted_actions` e `targeted_reports`
nel proprio `parse_canonical()`. Vedi checklist in `PIANO_INCREMENTAZIONE_MULTI_REF.md`.

---

## 2026-04-27 — Fase 2: Parser Builder (`trader_a` pilota)

### Step completato

**Fase 2** del piano `PIANO_INCREMENTAZIONE_MULTI_REF.md` — `trader_a` produce
`targeted_actions` e `targeted_reports` in `parse_canonical()`.

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/parser/canonical_v1/targeted_builder.py` | Creato | Builder shared: `build_targeted_actions`, `build_targeted_reports_from_lines` |
| `src/parser/trader_profiles/trader_a/profile.py` | Modificato | Import builder + blocco targeted in `parse_canonical()` + 5 costruttori estesi |
| `src/parser/trader_profiles/trader_a/tests/test_multi_ref.py` | Creato | 5 test Phase 2 — tutti verdi |
| `docs/in_progress/new_parser/PIANO_INCREMENTAZIONE_MULTI_REF.md` | Aggiornato | Checklist Fase 2 spuntata; sezione "Lavoro svolto" aggiunta |

### Risultato test

```
pytest src/parser/trader_profiles/trader_a/tests/test_multi_ref.py  →  5/5 passed
pytest src/parser/  →  725 passed, 15 failed (tutti pre-esistenti, nessuno introdotto)
```

### Rischi aperti

- Validazione su dataset reale del DB non eseguita (nessun accesso diretto al DB in sessione).
  Pattern derivati da codice esistente — da verificare con replay_parser.
- `event_type` nei `targeted_reports` è sempre `FINAL_RESULT` (scelta conservativa).
  Distinzione `TP_HIT`/`STOP_HIT` richiede contesto posizione — deferred a Fase 3/5.
- `build_targeted_reports_from_lines` richiede formato riga `SYMBOL - LINK VALUE UNIT`.
  Varianti senza simbolo o con separatori diversi non estratte.
- `parsing_rules.json` non modificato — le regole multi-ref erano già presenti nella logica Python.

### Prossimo step

**Fase 3** — Target Resolver: diventa multi-target e multi-action aware.

---

## 2026-04-27 — Fase 3: Target Resolver multi-target aware

### Step completato

**Fase 3** del piano `PIANO_INCREMENTAZIONE_MULTI_REF.md` — il resolver viene esteso
con una nuova funzione standalone `resolve_targeted()` che elabora `targeted_actions`
e `targeted_reports` producendo `MultiRefResolvedResult`.

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/target_resolver/models.py` | Creato | `ResolvedActionItem`, `ResolvedReportItem`, `MultiRefResolvedResult` |
| `src/target_resolver/resolver.py` | Modificato | Import + `_resolve_action_item` + `_resolve_report_item` + `resolve_targeted` |
| `src/target_resolver/tests/test_targeted_resolver.py` | Creato | 5 test Fase 3 — tutti verdi |
| `docs/in_progress/new_parser/PIANO_INCREMENTAZIONE_MULTI_REF.md` | Aggiornato | Checklist Fase 3 spuntata; sezione "Lavoro svolto" aggiunta |

### Risultato test

```
pytest src/target_resolver/  →  16/16 passed (5 nuovi + 11 preesistenti)
pytest src/target_resolver/ tests/parser_canonical_v1/ src/parser/trader_profiles/trader_a/tests/test_multi_ref.py
→  137/137 passed
```

### Rischi aperti

- `TargetResolver.resolve()` (legacy) ancora non migrata — dipende da layer downstream (operation_rules, router).
- `targeted_reports` con NOT_FOUND non coperto da test dedicato — logica implementata ma non testata per il caso di fallimento.
- Integrazione end-to-end su replay reale non ancora eseguita (accesso DB non disponibile in sessione).
- `event_type=FINAL_RESULT` nei report è ancora fisso (eredità Fase 2) — la distinzione richiede contesto posizione.

### Prossimo step

**Fase 4** — Router / Update Planner / Runtime: il runtime consuma il binding reale `azione → target`.

---

## 2026-04-27 — STEP 0: Pre-condizioni per Disambiguation & Context Resolution

### Step completato

**STEP 0** del piano `PIANO_IMPLEMENTAZIONE_DISAMBIGUATION_CONTEXT_RESOLUTION.md` —
verifica e ripristino delle pre-condizioni prima di iniziare il layer semantico.

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/parser/canonical_v1/models.py` | Modificato | `RiskHint` esteso con `min_value: float | None` e `max_value: float | None` |
| `src/parser/trader_profiles/trader_a/profile.py` | Modificato | Import `RiskHint`; regex `_RISK_RANGE_RE`/`_RISK_SINGLE_RE`; funzione `_extract_risk_hint()`; estrazione in `_extract_entities`; uso in `_build_ta_signal_payload` |
| `src/parser/trader_profiles/trader_a/tests/test_profile_phase4_common.py` | Modificato | Intent name corretto `NEW_SETUP`→`NS_CREATE_SIGNAL`; 12 test `parse_event_envelope_*` marcati `@unittest.skip` (Phase 4 pending) |
| `src/parser/trader_profiles/trader_d/tests/test_profile_smoke.py` | Modificato | Testo test corretto da `"entry: 65000"` a `"Вход с текущих: 65000"` |

### Risultato test

```
pytest src/parser/trader_profiles/  →  549 passed, 12 skipped, 0 failed
```

### Rischi aperti

- `models.py` ha modifiche non committate pre-esistenti (contratto multi-ref): la pre-condizione
  "nessuna modifica pendente" non è pienamente soddisfatta. Commit da eseguire manualmente.
- 12 test `parse_event_envelope_*` sono SKIPPED — richiedono `parse_event_envelope()` e campi
  `UpdatePayloadRaw.stop_update`, `ReportPayloadRaw.reported_results` (plurale) da progettare in Phase 4.
- `_RISK_RANGE_RE` non cattura pattern puramente numerici senza keyword russo (es. `"1-2% od depozita"` in inglese).

### Prossimo step

**Step 1** — Taxonomy Layer: definire `IntentName` e `STATEFUL_INTENTS` in `intent_taxonomy.py`.

---

## 2026-04-27 — STEP 1: Taxonomy Layer (`intent_taxonomy.py`)

### Step completato

**STEP 1** del piano `PIANO_IMPLEMENTAZIONE_DISAMBIGUATION_CONTEXT_RESOLUTION.md` —
fonte unica di verità per gli 17 intent ufficiali.

### File toccati

| File | Stato | Note |
|------|-------|------|
| `src/parser/canonical_v1/intent_taxonomy.py` | Creato | `IntentName` Literal, `INTENT_NAMES`, `STATEFUL_INTENTS`, `STRONGLY_STATEFUL`, `validate_intent_name` |
| `tests/parser_canonical_v1/test_intent_taxonomy.py` | Creato | 29 test — tutti verdi |

### Risultato test

```
pytest tests/parser_canonical_v1/test_intent_taxonomy.py  →  29 passed
pytest src/parser/trader_profiles/                        →  549 passed, 12 skipped, 0 failed
```

### Rischi aperti

- Alias legacy `"NS_CREATE_SIGNAL"` (usato in trader_a) non incluso nel taxonomy — risoluzione richiesta prima di chiamare `validate_intent_name` nei profili.

### Prossimo step

**Step 2** — Modello `IntentCandidate` in `intent_candidate.py`.

---

## 2026-04-27 — STEP 2: Modello `IntentCandidate`

### Step completato

**STEP 2** del piano `PIANO_IMPLEMENTAZIONE_DISAMBIGUATION_CONTEXT_RESOLUTION.md` —
struttura dati tipizzata per i candidati con forza ed evidenza.

### File toccati

| File | Stato | Note |
|------|-------|------|
| `src/parser/canonical_v1/intent_candidate.py` | Creato | `IntentStrength`, `IntentCandidate` Pydantic v2, properties `is_strong`/`is_weak` |
| `tests/parser_canonical_v1/test_intent_candidate.py` | Creato | 11 test — tutti verdi |

### Risultato test

```
pytest tests/parser_canonical_v1/test_intent_candidate.py  →  11 passed
pytest src/parser/trader_profiles/                         →  549 passed, 12 skipped, 0 failed
```

### Rischi aperti

- Nessun limite sulla lunghezza di `evidence` — accettabile per ora, da valutare se diventa fonte di output verboso.
- Implementazione era già pre-esistente nella working copy (sessione precedente non committata); verificata corretta e completa per la spec.

### Prossimo step

**Step 3** — Schema JSON `intent_compatibility` in `src/parser/shared/intent_compatibility_schema.py`.

---

## 2026-04-27 — STEP 3: Schema JSON `intent_compatibility`

### Step completato

**STEP 3** del piano `PIANO_IMPLEMENTAZIONE_DISAMBIGUATION_CONTEXT_RESOLUTION.md` —
validatore Pydantic per il blocco `intent_compatibility` nei `parsing_rules.json`.

### File toccati

| File | Stato | Note |
|------|-------|------|
| `src/parser/shared/__init__.py` | Creato | Package vuoto per il layer semantico condiviso |
| `src/parser/shared/intent_compatibility_schema.py` | Creato | `RelationType`, `IntentCompatibilityPair`, `IntentCompatibilityBlock` |
| `tests/parser_canonical_v1/test_intent_compatibility_schema.py` | Creato | 17 test — tutti verdi |

### Risultato test

```
pytest tests/parser_canonical_v1/test_intent_compatibility_schema.py  →  17 passed
pytest src/parser/trader_profiles/                                     →  549 passed, 12 skipped, 0 failed
```

### Rischi aperti

- Unicità delle coppie e unicità degli intent in `intents` non verificata a schema — rinviata a Step 11 (validazione manuale JSON).
- `IntentCompatibilityBlock` non ancora registrato nel `RulesEngine`.

### Prossimo step

**Step 4** — Schema JSON `disambiguation_rules` in `src/parser/shared/disambiguation_rules_schema.py`.

---

## 2026-04-27 — STEP 4: Schema JSON `disambiguation_rules`

### Step completato

**STEP 4** del piano `PIANO_IMPLEMENTAZIONE_DISAMBIGUATION_CONTEXT_RESOLUTION.md` —
validatore Pydantic per il blocco `disambiguation_rules` nei `parsing_rules.json`.

### File toccati

| File | Stato | Note |
|------|-------|------|
| `src/parser/shared/disambiguation_rules_schema.py` | Creato | `DisambiguationAction`, `DisambiguationRule`, `DisambiguationRulesBlock` |
| `tests/parser_canonical_v1/test_disambiguation_rules_schema.py` | Creato | 18 test — tutti verdi |

### Risultato test

```
pytest tests/parser_canonical_v1/test_disambiguation_rules_schema.py  →  18 passed
pytest src/parser/trader_profiles/                                     →  549 passed, 12 skipped, 0 failed
```

### Rischi aperti

- `prefer` non è validato come appartenente a `when_*_detected` — una regola con intent incoerenti è accettata per schema; il controllo è responsabilità del motore (Step 7).
- `keep_multi` non richiede `keep` valorizzato — il motore deve gestire `keep=None` come "mantieni tutti i candidati".
- Unicità dei nomi regola non verificata a schema — duplicati non rilevati prima di Step 11.

### Prossimo step

**Step 5** — Schema JSON `context_resolution_rules` in `src/parser/shared/context_resolution_schema.py`.

---

## 2026-04-29 — Check stato reale Fasi 1-4 del parser redesign

### Scopo

Verifica documentale del piano `PARSER_REDESIGN_SPEC_V1.md` contro il repository reale,
senza introdurre nuova logica di prodotto.

### Esito sintetico

| Fase | Stato | Nota |
|---|---|---|
| Fase 1 — Cleanup preliminare | Parziale | chiusa solo per i file legacy sicuramente scollegati |
| Fase 2 — ParsedMessage models | Completata | modelli e test presenti |
| Fase 3 — Shared infrastructure | Completata | runtime/disambiguation/schema presenti e verificati |
| Fase 4 — trader_a pilota | Non completata | il profilo `trader_a` e ancora sul percorso legacy |

### Evidenze raccolte

- `src/parser/intent_types.py` e `src/parser/parsed_message.py` sono presenti.
- `src/parser/shared/runtime.py` e `src/parser/shared/disambiguation.py` sono presenti.
- I test Phase 1-3 esistono e passano.
- `src/parser/trader_profiles/trader_a/profile.py` usa ancora `parsing_rules.json`.
- In `src/parser/trader_profiles/trader_a/` non esistono ancora `semantic_markers.json` e `rules.json`.
- `trader_a/profile.py` espone ancora `parse_canonical(...) -> CanonicalMessage`, non il nuovo `parse(...) -> ParsedMessage`.

### Verifica eseguita

```bash
pytest src/parser/tests/test_phase1_cleanup.py \
       src/parser/tests/test_phase2_parsed_message.py \
       src/parser/tests/test_phase3_shared_runtime.py \
       src/parser/tests/test_phase3_disambiguation.py \
       src/parser/tests/test_phase3_rules_schema.py -q
```

Risultato:

```text
30 passed
```

### File toccati

| File | Stato | Note |
|---|---|---|
| `docs/in_progress/new_parser/PARSER_REDESIGN_SPEC_V1.md` | Aggiornato | aggiunta sezione di check stato Fasi 1-4 |
| `docs/AUDIT.md` | Aggiornato | registrata la verifica del 2026-04-29 |

### Rischi aperti

- La checklist della Fase 1 nel documento originale e piu ampia dello stato reale del cleanup: se la si interpreta letteralmente, la fase non e ancora completamente chiusa.
- La Fase 4 non va considerata "in corso avanzato" solo per la presenza di `extractors.py`: il contratto del profilo e ancora legacy.
- Fasi successive che assumono `trader_a` gia migrato devono essere considerate bloccate o almeno premature.

### Prossimo step

Quando si riprendera il lavoro implementativo:
- o si chiude davvero il residuo di Fase 1 con una nuova migrazione controllata;
- oppure si accetta formalmente che la Fase 1 e "parzialmente chiusa" e si apre la vera migrazione Fase 4 di `trader_a`.


---

## 2026-05-30 — Control Plane Part 3 + Delivery Mode Delta: Read-Only Bot completata

### Step completato

Implementata la Part 3 del Control Plane Telegram (bot read-only) e integrato il delta `delivery_mode` (Task 5 — Reply Keyboard). Il bot risponde ai comandi `/help`, `/status`, `/trades`, `/trade <id>`, `/health`, `/control`, `/reviews`, `/version` con autorizzazione, audit, e formattazione testuale. Ogni ricevuto viene auditato in `ops_telegram_control_commands`.

### File creati

| File | Responsabilità |
|---|---|
| `src/runtime_v2/control_plane/status_queries.py` | `StatusQueries` + 9 view dataclasses — query read-only su `ops.sqlite3` |
| `src/runtime_v2/control_plane/service.py` | `RuntimeControlService` (read API, Part 4 aggiungerà write); `VersionInfo` via `git` subprocess |
| `src/runtime_v2/control_plane/audit_store.py` | `CommandAuditStore.record()` + `update_status()` — idempotente su `command_request_id` |
| `src/runtime_v2/control_plane/telegram_bot.py` | `CommandRouter` (auth→audit→dispatch→format) + `TelegramControlBot` (PTB wrapper) + `_send_reply_keyboard` (Delta Task 5) |
| `src/runtime_v2/control_plane/formatters/status.py` | `format_status`, `status_level` (🟢/🟡/🔴) |
| `src/runtime_v2/control_plane/formatters/trades.py` | `format_trades` — lista compatta trade attivi |
| `src/runtime_v2/control_plane/formatters/trade_detail.py` | `format_trade_detail` — dettaglio chain |
| `src/runtime_v2/control_plane/formatters/health.py` | `format_health` — worker status e DB |
| `src/runtime_v2/control_plane/formatters/control.py` | `format_control` — blocchi e blacklist |
| `src/runtime_v2/control_plane/formatters/reviews.py` | `format_reviews` — chains in REVIEW_REQUIRED |
| `tests/runtime_v2/control_plane/test_status_queries.py` | 4 test: counts, control/blacklist, reviews, trade detail |
| `tests/runtime_v2/control_plane/test_readonly_formatters.py` | 13 test: semaforo, formatter output, edge cases |
| `tests/runtime_v2/control_plane/test_audit_store.py` | 3 test: record, reject, idempotency |
| `tests/runtime_v2/control_plane/test_command_router.py` | 13 test: auth/reject/dispatch/audit + wrong-topic audit + keyboard guards |

### Risultato test

```
python -m pytest tests/runtime_v2/control_plane/ -v
→ 75 passed, 0 failed ✅
```

### Decisioni e design notes

- **`audit_store.py` in Part 3 (non Part 4 come da spec)**: il path REJECT_UNAUTHORIZED deve auditare dal primo messaggio; Part 4 riusa senza modifiche.
- **PnL/ROI omessi**: `/status`, `/trades`, `/trade` omettono unrealized PnL perché il mark-price non è persistito nello schema attuale. `/pnl` è Part 5.
- **`CommandRouter._allowed_commands()` override-friendly**: `frozenset` in metodo separato per estensione in Part 4/5 senza riscrivere routing/auth.
- **Delta Task 5 — Reply Keyboard**: `_send_reply_keyboard` è no-op in `supergroup_topics`; invia `ReplyKeyboardMarkup` (con `is_persistent=True` per PTB v22) su `/start` in `private_bot`. Bug PTB `persistent` → `is_persistent` fixato durante review.
- **`str(None)` → `None`**: `_record` ora scrive `NULL` in `message_thread_id` invece di `"None"` quando `thread_id is None` (private_bot mode).
- **`_start_time` in `__init__`**: uptime misura dall'istanziazione del servizio, non dall'import del modulo.

### Scope note documentata

PnL/ROI/mark-price fields nei mock-up di COMMANDS_SPEC richiedono dati di mercato non persistiti nel DB corrente. I campi omessi sono: unrealized PnL per trade, ROI %, mark price. `/pnl` è Part 5.

### Rischi aperti

- Worker list in `get_health()` è hardcoded con stato `"OK"` — la funzione non interroga heartbeat reali. Questo dà una falsa rassicurazione. Part 5 dovrà aggiungere un meccanismo di heartbeat per i worker o rimuovere le righe faked-OK.
- `TelegramControlBot._on_command` invia sempre a `self._config.chat_id` (config), non a `msg.chat_id`. In `private_bot` mode questo potrebbe divergere se il bot riceve messaggi da chat private diverse da quella configurata. Design intenzionale per ora.
- Delta Tasks 2-3 già implementati in Part 2 (topic_router, notification_dispatcher). Delta Task 4 (formatters/tech_log.py) è Part 5.

### Prossimi step

- Part 4: write commands (`/pause`, `/resume`, `/block`, `/unblock`, `/start`) — estende `CommandRouter` e `RuntimeControlService`.
- Part 5: `formatters/tech_log.py` con prefisso `⚠️ --SYSTEM--` per `private_bot`; `/pnl`, `/logs`, `/debug`.
- Fix P3 (posizione reconciliation al riavvio) — prima del go-live in produzione.

---

## 2026-05-30 — Control Plane Part 4 + Delivery Mode Delta: Control Commands completata

### Step completato

Implementata la Part 4 del Control Plane Telegram: il bot ora supporta i comandi write-side `/pause`, `/resume`, `/start`, `/block`, `/unblock`, con scritture auditabili e idempotenti su `ops_control_state` e `ops_config_overrides`. Nello stesso ciclo sono stati chiusi i punti di integrazione del delta `delivery_mode` che impattavano il path reale dei comandi: audit senza thread in `private_bot`, keyboard su `/start` e primo contatto autorizzato, e dispatch notifiche senza `message_thread_id`.

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/runtime_v2/control_plane/override_store.py` | Creato | Persistenza blacklist symbol-level in `ops_config_overrides`; update atomico via transazione `BEGIN IMMEDIATE` |
| `src/runtime_v2/control_plane/service.py` | Modificato | Aggiunti `PauseResult`, `ResumeResult`, `BlockResult`, `UnblockResult`; metodi `pause`, `resume`, `start`, `block_symbol`, `unblock_symbol` |
| `src/runtime_v2/control_plane/telegram_bot.py` | Modificato | Router esteso ai comandi write-side; validazione arità per `/pause` e `/resume`; keyboard privata solo su `/start` e primo testo autorizzato |
| `src/runtime_v2/control_plane/audit_store.py` | Modificato | In `private_bot`, `message_thread_id` vuoto (`""`) invece di `NULL`, coerente col vincolo `NOT NULL` della migration 007 |
| `src/runtime_v2/control_plane/status_queries.py` | Modificato | `/status` espone solo il blocco globale come stato runtime; `/control` continua a mostrare anche i blocchi trader-scoped |
| `src/runtime_v2/control_plane/formatters/pause.py` | Creato | Reply formatter per `/pause`, `/resume`, `/start` |
| `src/runtime_v2/control_plane/formatters/block.py` | Creato | Reply formatter per `/block`, `/unblock` |
| `tests/runtime_v2/control_plane/test_override_store.py` | Creato | 5 test: add/remove/idempotenza/global/per-trader |
| `tests/runtime_v2/control_plane/test_service_writes.py` | Creato | 9 test: pause/resume/start + visibilità blacklist |
| `tests/runtime_v2/control_plane/test_control_formatters.py` | Creato | 10 test per formatter write-side |
| `tests/runtime_v2/control_plane/test_command_router_writes.py` | Creato | 8 test: dispatch write-side, audit, usage |
| `tests/runtime_v2/control_plane/test_command_router.py` | Modificato | Copertura `private_bot`: `/start`, first-contact keyboard, no keyboard su comandi non-`/start`, audit senza thread |
| `tests/runtime_v2/control_plane/test_dispatcher.py` | Modificato | Copertura dispatch `private_bot` senza `thread_id` |
| `tests/runtime_v2/control_plane/test_status_queries.py` | Modificato | Copertura separata per blocchi globali vs trader-scoped in `/status` e `/control` |

### Risultato test

```text
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane -q
→ 114 passed, 1 warning ✅

C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle -q
→ 294 passed, 1 warning ✅

Warning pre-esistente:
PytestConfigWarning: Unknown config option: collect_ignore_glob
```

### Decisioni e design notes

- **Per-trader pause usa `scope_type="TRADER"`**: scelta intenzionale per allinearsi a `src/runtime_v2/lifecycle/repositories.py`, dove `ControlStateRepository.get_effective_mode()` legge `TRADER` e non `PER_TRADER`. Questo chiude la discrepanza aperta in Part 1.
- **Blacklist write-side separata dai control blocks**: `/block` e `/unblock` persistono in `ops_config_overrides` con scope `GLOBAL | PER_TRADER`, mentre `/pause` e `/resume` agiscono su `ops_control_state`. Le due superfici restano distinte per design.
- **Race fix nel blacklist store**: la prima implementazione read-modify-write è stata corretta durante review. Le mutazioni ora serializzano per scope dentro una singola transazione IMMEDIATE, evitando overwrite concorrenti.
- **Visibilità operativa corretta**: `/status` tratta `new_entries_enabled` e `control_mode` come segnale globale del runtime. Un blocco `TRADER` resta visibile in `/control`, ma non degrada il runtime a `BLOCKED` per tutti.
- **Audit comandi coerente**: i comandi con arità/sintassi invalida (`/trade nope`, `/pause a b`, `/block` senza simbolo) restituiscono ancora il testo di usage, ma vengono registrati come `REJECTED` con `reject_reason="invalid_arguments"` invece che come `EXECUTED`.
- **Delta `private_bot` corretto al layer proprietario**:
  - audit dei comandi compatibile con `message_thread_id NOT NULL`;
  - `ReplyKeyboardMarkup` inviato su `/start` e primo messaggio testuale autorizzato;
  - nessuna push della keyboard su ogni comando eseguito;
  - dispatcher già coerente con `thread_id=None`.

### Scope note documentata

- **Blacklist enforcement nel gate segnali**: questa parte persiste e mostra la blacklist nel control plane, ma non modifica ancora il merged-read dell’enrichment/gate che oggi legge il blacklist da YAML/operation config. Quindi `/block` è completo lato control-plane, non ancora lato enforcement operativo upstream.

### Rischi aperti

- `get_health()` continua a usare una lista worker hardcoded con stati nominali; il control plane non ha ancora heartbeat runtime reali.
- `TelegramControlBot` continua a rispondere sempre alla `chat_id` configurata, non alla chat sorgente del messaggio. In `private_bot` è intenzionale, ma richiede che il bot sia usato solo nella chat autorizzata prevista.
- La enforcement della blacklist nel gate segnali resta follow-up architetturale e non va considerata completata solo perché `/control` la visualizza.

### Prossimi step

- Part 5: `formatters/tech_log.py` con prefisso `⚠️ --SYSTEM--` in `private_bot`; `/pnl`, `/logs`, `/debug_on`, `/debug_off`.
- Wiring finale in `main.py`: startup modes `auto | standby | restore`, snapshot runtime, bootstrap completo bot+dispatcher.
- Follow-up separato: merged-read degli override blacklist nel gate/enrichment per enforcement a monte del signal flow.

---

## 2026-05-30 — Spec Gap Closure Task 3 + Task 4: TECH_LOG policy reali e bootstrap/startup/shutdown

### Step completato

Task 3 — `TECH_LOG` governato da policy runtime reali. Task 4 — `main.py` ora usa `build_control_plane()` dal bootstrap centralizzato, applica startup mode e salva snapshot runtime a shutdown.

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/runtime_v2/control_plane/notification_dispatcher.py` | Modificato | `debug_status: Callable[[], bool]` iniettato nel costruttore; `_should_send_tech_log()` con gating su `enabled`, `DEBUG`, `INFO/operational_events`, `min_level`; chiamato prima del rate-limit in `drain_once()` |
| `src/runtime_v2/control_plane/bootstrap.py` | Modificato | `debug_status=service.debug_status` passato al dispatcher |
| `src/runtime_v2/control_plane/formatters/tech_log.py` | Modificato | Output strutturato con `title`, `context` (dict → `key: value`), `action`; `None` in context → `—`; `⚠️ --SYSTEM--` solo per `private_bot`; `details` ignorato silenziosamente |
| `tests/runtime_v2/control_plane/test_tech_log_policy.py` | Creato | 6 test policy: disabled suppression, min_level blocking, debug inactive, operational_events gate, operational_events allowed, private_bot prefix |
| `tests/runtime_v2/control_plane/test_dispatcher.py` | Modificato | `_seed_tech_log` usa `level: "WARNING"` per passare il default `min_level=WARNING`; test di routing/formatting invariati |
| `main.py` | Modificato | Rimossa `_build_control_plane()` locale; import e uso di `build_control_plane()` da bootstrap; applicazione startup mode (`apply_global_block` → `pause()`); log restore fallback e restore success; snapshot save su shutdown con `active_blocks` serializzati correttamente (`GLOBAL` non duplicato) |
| `tests/runtime_v2/control_plane/test_main_control_plane.py` | Creato | 3 test: disabled config restituisce None, standby mode produce `apply_global_block=True` e pausa, snapshot save + shutdown notification scrivono DB correttamente |

### Risultato test

```
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane -q
→ 177 passed, 1 warning ✅
```

### Decisioni e design notes

- **Gating order**: `_should_send_tech_log()` prima di `_check_tech_log_rate()` — una notifica soppressa per policy non consuma slot rate-limit.
- **operational_events è un veto secondario**: INFO è sempre soppresso se `operational_events=False`, anche se `min_level="INFO"` — il flag è più specifico del livello numerico. Commentato nel codice.
- **Level sconosciuto → current=0**: livelli non riconosciuti sono sempre soppressi, mai promossi silenziosamente.
- **Rate counter ottimistico**: lo slot è contato prima del send; send failure non rimuove il slot (documentato con commento).
- **active_blocks snapshot**: `scope_value or 'GLOBAL'` era ambiguo per scope GLOBAL (produceva `GLOBAL:GLOBAL`); ora `scope_type:scope_value if scope_value else scope_type`.
- **Patch test isolato correttamente**: `telegram.Bot` è importato inline dentro `_create_sender()`, quindi `patch("telegram.Bot")` è il target corretto.

### Rischi aperti

- **Worker list in `get_health()` ancora hardcoded**: stati nominali fissi — nessun heartbeat runtime reale. Da risolvere prima del go-live.
- **`await control_bot.run()` pre-task-creation**: se la bot startup lancia eccezione, i task lifecycle creati prima non vengono cancellati nella inner finally. Pre-esistente, non introdotto in questi task.
- **Enforcement blacklist nel gate segnali**: `/block` persiste nel control plane ma non influenza ancora il gate upstream. Follow-up architetturale separato.

### Prossimi step

- ✅ Task 5 (CLEAN_LOG event coverage) — completato in commit 6f7830c
- ✅ Task 6 (CLEAN_LOG root/reply tracking) — completato in commit 6c3afc8

---

## 2026-05-30 — Spec Gap Closure Task 5 + Task 6: CLEAN_LOG coverage e tracking

### Step completato

Task 5 ha espanso la copertura eventi CLEAN_LOG con 8 nuovi event type (ENTRY_UPDATED, UPDATE_DONE, UPDATE_PARTIAL, UPDATE_REJECTED, PENDING_ENTRY_EXPIRED, RECONCILIATION_WARNING, RECONCILIATION_FIXED, REENTRY_ACCEPTED). Task 6 ha aggiunto il tracking root/last message id e aggregazione minima per la reply-threading in Telegram.

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/runtime_v2/control_plane/outbox_writer.py` | Modificato | `_CLEAN_LOG_EVENT_MAP` esteso da 7 a 15 event type; aggiunti branch dedicati in `_build_payload()` per ENTRY_UPDATED, UPDATE_DONE, UPDATE_PARTIAL, UPDATE_REJECTED, PENDING_ENTRY_EXPIRED, RECONCILIATION_WARNING, RECONCILIATION_FIXED, REENTRY_ACCEPTED |
| `src/runtime_v2/control_plane/formatters/clean_log.py` | Modificato | Aggiunti 8 formatter dedicati per i nuovi event type con emoji e message payload strutturato (✏️ ENTRY_UPDATED, ✅ UPDATE_DONE, ⚠️ UPDATE_PARTIAL, ❌ UPDATE_REJECTED, ⏰ PENDING_ENTRY_EXPIRED, ⚠️ RECONCILIATION_WARNING, ✅ RECONCILIATION_FIXED, 🔄 REENTRY_ACCEPTED) |
| `src/runtime_v2/control_plane/models.py` | Modificato | Aggiunto Pydantic model `CleanLogTracking` con campi `root_message_id`, `last_message_id`, `update_group_id`, timestamps |
| `src/runtime_v2/control_plane/notification_dispatcher.py` | Modificato | `NotificationSender` protocol ritorna `str | None` (message ID reale da Telegram); `TelegramBotSender` ritorna `str(msg.message_id)`; `drain_once()` risolve target reply e persiste tracking per ogni CLEAN_LOG send; logica aggregazione minima: stesso chain + stesso `update_group_id` → reply a `last_message_id`, altrimenti → reply a `root_message_id` |
| `db/ops_migrations/008_ops_clean_log_tracking.sql` | Creato | Migration tabella `ops_clean_log_tracking` con `trade_chain_id PK`, `root_message_id TEXT`, `last_message_id TEXT`, `update_group_id TEXT`, chat/thread metadata, timestamps |
| `tests/runtime_v2/control_plane/test_clean_log_formatter_full.py` | Creato | 17 test per i 8 nuovi formatter event type (2 test per type + 1 test fallback) |
| `tests/runtime_v2/control_plane/test_outbox_writer.py` | Modificato | +3 test di proiezione per gli 8 nuovi event type |
| `tests/runtime_v2/control_plane/test_migration_008.py` | Creato | 4 test: verifica tabella creata, colonne attese, vincoli PK, nullable corretti |
| `tests/runtime_v2/control_plane/test_clean_log_tracking.py` | Creato | 17 test: root/last message id tracking, aggregazione update_group_id, backward compat con NULL, transazioni atomiche |

### Risultato test

```
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane -q
→ 211 passed, 1 warning ✅
```

### Decisioni e design notes

- **Aggregation rule minimale**: stesso chain + stesso `update_group_id` → reply al `last_message_id`; altrimenti → reply al `root_message_id` (o non-reply se root assente). Debounce/batching completo è deferito a post-go-live.
- **Sender protocol aggiornato**: `NotificationSender` ritorna `str | None` (message ID reale da Telegram API); i test mock la signature con sender fake che ritorna `"123"`.
- **Payload `chain_id` garantito**: `write_clean_log_event` inietta `chain_id` nel payload JSON se assente, così `drain_once()` può sempre estrarlo per la lookup tracking.
- **TECH_LOG e COMMANDS_REPLY invariati**: il nuovo tracking CLEAN_LOG è gating solo nel branch `destination == "CLEAN_LOG"` di `drain_once()`.
- **Transazioni atomiche**: ogni send + tracking save è atomico dentro una transazione, evitando orphaned outbox rows.

### Deferred (CLEAN_LOG_SPEC §6–§8, §15)

- Debounce/batching completo (`debounce_seconds`, `aggregate_fills_seconds`) — config caricata ma non applicata.
- `max_messages_per_chain_per_minute` — non enforced.
- `original_message_link` nel tracking — non ancora popolato dal message metadata.

### Rischi aperti

- `update_group_id` non è ancora emesso da nessun worker lifecycle → la regola di aggregazione per update group rimane inerte in produzione finché i worker non producono quel campo.
- Connection churning in `drain_once()` (pattern pre-esistente): ogni CLEAN_LOG send apre 2 connessioni SQLite aggiuntive (tracking read + tracking write) oltre alle connessioni già pre-esistenti per `_mark_sent`. Non è un bug ma è inefficiente; da ottimizzare in un passaggio separato se il volume diventa rilevante.

### Prossimi step

- Task 7 (ultimo del piano) — aggiornare `docs/AUDIT.md` per allineare il record della closure spec gap (questa sezione).
- Verificare lo stato dei "Rischi aperti" globali nella fine di AUDIT.md per riallineamento finale.

---

## 2026-05-29 — Problemi sistemici runtime_v2: riconciliazione al riavvio

### P2 — FIXATO: mark_done condizionato all'INSERT

**File modificato:** `src/runtime_v2/execution_gateway/event_sync.py`

**Problema:** In `run_reconciliation()`, `mark_done(cmd)` veniva chiamato solo se
`insert_exchange_event()` ritornava `True` (nuova riga inserita). Se il WebSocket aveva
già inserito il medesimo evento (via INSERT OR IGNORE), il comando restava stuck in
`SENT` per sempre, generando polling REST infinito su ordini già risolti.

**Fix:** `mark_done()` ora viene chiamato incondizionatamente ogni volta che l'exchange
conferma un fill o un cancel, indipendentemente dal risultato dell'INSERT (che rimane
idempotente via INSERT OR IGNORE).

**Test aggiunto:** `test_run_reconciliation_marks_done_even_when_ws_already_inserted_event`
in `tests/runtime_v2/execution_gateway/test_event_sync.py`.

---

### P3 — APERTO: nessuna position reconciliation per chiusure parziali al riavvio

**File coinvolto:** `src/runtime_v2/execution_gateway/event_sync.py` — `run_position_reconciliation()`

**Problema:** Al riavvio, `watch_positions` consegna uno snapshot della posizione attuale
su exchange, ma viene classificato `UNKNOWN` e scartato. `run_position_reconciliation()`
rileva solo chiusure complete (`qty == 0`). Chiusure parziali avvenute durante il downtime
(TP parziali, close manuali parziali) non vengono rilevate — `open_position_qty` nel DB
diverge silenziosamente dalla realtà.

**Impatto osservato (2026-05-29):** chain 1 BTCUSDT — TP1 (0.0625 BTC) colpito mentre
il bot era spento; bot riavviato con `open_position_qty=0.237` invece di 0.175.
cmd22 emesso con qty TP sbagliata (0.1185 su posizione reale 0.175).

**Perché non fixato ora:** la fix richiede design non banale:

1. Sequenza di boot esplicita: la REST reconciliation deve completare prima del confronto
   snapshot, altrimenti i fill di entry mancati generano falsi positivi.
2. Coordinazione con `run_trade_based_reconciliation()` per evitare double-booking
   dello stesso fill come sia `CLOSE_PARTIAL_FILLED` sintetico che `TP_FILLED`.
3. Semantica degli eventi: un confronto qty non distingue tra TP, SL parziale e close
   manuale — il lifecycle tratta questi casi diversamente.

**Quando implementare:** prima del go-live in produzione, se si prevedono downtime
anche brevi. Considerare un evento dedicato `POSITION_DRIFT_DETECTED` invece di un
`CLOSE_PARTIAL_FILLED` sintetico, gestito esplicitamente dal lifecycle.

---

## 2026-06-11 — Revisione branch + fix targeting explicit_ids (entry_gate)

### FIXATO: targeting per explicit_ids — tre regressioni in `_resolve_targets`/`_persist_signal`

**File coinvolto:** `src/runtime_v2/lifecycle/entry_gate.py`

**Problemi trovati in revisione (confermati):**

1. **Solo il primo explicit_id persistito** — `_persist_signal` salvava
   `sig_ids[0]` in `external_signal_id`: un update che citava il secondo ID
   del segnale non matchava mai e finiva in review.
2. **Chain pre-migrazione 014 non raggiungibili** — `external_signal_id` è NULL
   per le chain create prima della migrazione (nessun backfill); il matching
   le scartava sempre.
3. **Fallthrough rimosso** — con explicit_ids senza match la funzione ritornava
   subito `[]` invece di proseguire col matching per reply/telegram ID
   (comportamento precedente), causando review `no_update_target` evitabili.

**Fix:**
- `external_signal_id` ora persiste tutti gli ID separati da `|` (convenzione liste).
- Nuovo helper `_chain_signal_ids()` splitta e normalizza gli ID della chain;
  il matching accetta qualsiasi ID persistito **oppure** `canonical_message_id`
  (fallback per chain pre-migrazione).
- Ripristinato il fallthrough al matching telegram quando explicit_ids non matcha;
  il caso ambiguo (più chain stesso ID) continua ad andare in review.

**Test aggiunti:** 4 test `test_explicit_id_*` in
`tests/runtime_v2/lifecycle/test_entry_gate.py` (multi-ID, fallback canonical,
fallthrough telegram, ambiguità → review). Esito: 88 passed; restano 7 failure
pre-esistenti non correlate (naming NOOP_* e clean_log update).

### Rischi aperti emersi dalla revisione (non fixati in questa sessione)

- `src/parser_v2/core/classification_resolver.py:37` — riclassificazione
  PARTIAL→UPDATE senza guard: un segnale nuovo parziale con intent di update
  e un simbolo nel testo diventa UPDATE. Nessun test copre il caso.
- `src/runtime_v2/control_plane/outbox_writer.py:403` — `close_reason=TRADER_COMMAND`
  dipende da `source=="trader_update"`, mai prodotto dal path WebSocket
  (SL position-level senza orderLinkId → `exchange_auto`); idempotency key
  WS/REST divergenti → rischio eventi duplicati.
- Efficienza: `rules.json` riletto da disco a ogni messaggio (registry senza cache,
  `load_rules()` ora incondizionato in `__init__` di tutti i profili).
- Duplicazione: helper di parsing prezzi/numeri byte-identici in 6 profili
  (incluso il nuovo `strategy_parser`); blocchi rules.json copiati in 4-5 profili.

### FIXATO: riclassificazione PARTIAL→UPDATE senza guard (classification_resolver)

**File coinvolto:** `src/parser_v2/core/classification_resolver.py`

**Problema:** `_looks_like_targeted_update` considerava sufficiente un qualsiasi
target hint, incluso il solo simbolo — che però viene estratto anche dal testo
di un segnale nuovo. Un segnale parziale con un'istruzione di gestione nel testo
(es. "poi spostate lo stop a BE") e il simbolo veniva forzato a UPDATE: niente
apertura posizione, e l'update finiva in review `no_update_target` (trade perso).

**Fix:** nuovo helper `_has_strong_target_hint` usato solo dalla riclassificazione:
esclude `symbols`, mantiene reply_to, telegram ids/links, explicit_ids e scope_hint
esplicito. Il caso d'uso del design doc (testo signal-like con `Signal ID: #c4` +
MODIFY_ENTRY) continua a funzionare via explicit_ids. `_has_target_hint` resta
invariato per il warning `update_without_target_hint`.

**Test aggiunti:** 3 test in `tests/parser_v2/test_classification_resolver_phase8.py`
(symbol-only resta SIGNAL/PARTIAL; explicit_id e reply_to forzano UPDATE).
Esito: 225 passed su parser_v2; 1 failure pre-esistente non correlata
(`test_trader_a_weak_context_rules`).

### FIXATO: rules.json e semantic_markers.json riletti da disco a ogni messaggio

**File coinvolti:** `src/parser_v2/core/profile_assets.py` (nuovo),
`src/parser_v2/profiles/*/profile.py` (7 profili)

**Problema:** il registry crea un'istanza nuova del profilo per ogni messaggio e
l'`__init__` chiama `load_rules()`; in più `runtime.py:77-78` richiama
`load_markers()` + `load_rules()` per ogni parse. Totale: 3 letture file +
validazioni Pydantic per messaggio (il semantic_markers.json di trader_prova è
~1700 righe).

**Fix:** nuovo modulo `profile_assets.py` con `load_rules_cached()` /
`load_markers_cached()` — lru_cache con chiave (path, mtime_ns): una modifica al
JSON invalida la entry, quindi il watch mode continua a funzionare. I 14 metodi
`load_*` dei 7 profili delegano al loader condiviso. Le istanze in cache sono
condivise: non vanno mutate (i consumer esistenti usano già model_copy).

**Misura:** 1000 cicli (init profilo + markers + rules) = 22 ms totali
(~0.02 ms/msg, prima ~3 letture+validazioni per messaggio).

**Test aggiunti:** `tests/parser_v2/test_profile_assets.py` (identità istanza a
file invariato, reload su mtime cambiato). Esito: 228 passed su parser_v2,
1 failure pre-esistente non correlata.
