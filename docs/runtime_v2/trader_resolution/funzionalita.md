# trader_resolution — Funzionalità

## Responsabilità

Il package `trader_resolution` si occupa di identificare quale trader ha inviato un messaggio raw, producendo un `ResolvedTraderContext`. Implementa una strategia config-first: prima cerca in `channels.yaml`, poi delega all'`EffectiveTraderResolver` legacy (testo → reply-chain).

## Componenti

### `models.py`

- **`ResolutionMethod`** — Literal con tutti i metodi di risoluzione possibili:
  - `source_topic_config` — match esatto (chat_id, topic_id) in channels.yaml
  - `source_chat_id` — match su chat_id in channels.yaml (senza topic)
  - `content_alias` — alias nel testo del messaggio
  - `content_alias_ambiguous` — alias trovato ma corrisponde a più trader
  - `reply_chain` — trovato risalendo la reply-chain
  - `reply_chain_alias` — alias trovato nella reply-chain
  - `source_chat_username`, `source_chat_title` — dal metadata del canale
  - `assume_trader` — fallback esplicito (solo in script di debug)
  - `unresolved` — nessuna risoluzione trovata

- **`ResolvedTraderContext`** — Pydantic model. Risultato della risoluzione. Campi: `raw_message_id`, `trader_id` (None se non risolto), `method`, `detail`, `is_ambiguous`, `resolved_at`.

- **`ParserDispatchCandidate`** — Pydantic model. Output finale dell'intake. Aggrega `RawMessageEnvelope`, `ResolvedTraderContext`, `parser_profile: str`, `parser_context: ParserContext`.

---

### `channel_config_resolver.py`

- **`ChannelEntry`** — dataclass frozen. Rappresenta una voce di `channels.yaml`. Campi: `chat_id`, `topic_id` (opzionale), `label`, `active`, `trader_id`, `parser_profile`, `blacklist`.

- **`ChannelConfigResolver`** — carica `channels.yaml` e fornisce lookup O(1) per `(source_chat_id, topic_id)`.

  **Metodi pubblici:**
  - `lookup(source_chat_id: str, topic_id: int | None) -> ChannelEntry | None` — cerca prima match esatto `(chat_id, topic_id)`, poi fallback a `(chat_id, None)` se topic_id non trovato.
  - `reload() -> None` — ricarica il file da disco (per hot-reload).
  - `is_globally_blacklisted(text: str) -> bool` — verifica se il testo contiene frasi della blacklist globale.

  **Regola parser_profile:** se `parser_profile` non è specificato in yaml, usa `trader_id` come default.

---

### `resolver.py`

- **`RuntimeV2TraderResolver`** — orchestratore della risoluzione trader con strategia config-first.

  **Costruttore:** riceve `ChannelConfigResolver` e `EffectiveTraderResolver`.

  **Metodo pubblico:** `resolve(envelope: RawMessageEnvelope) -> ResolvedTraderContext`

  **Logica:**
  1. Cerca in channels.yaml via `ChannelConfigResolver.lookup(source_chat_id, topic_id)`
  2. Se trovato e `entry.active` e `entry.trader_id` → restituisce immediatamente con `method=source_topic_config` (se entry ha topic_id) o `source_chat_id`
  3. Altrimenti → delega a `EffectiveTraderResolver` (text alias → reply-chain)
  4. Mappa il metodo restituito dal resolver legacy in `ResolutionMethod`
  5. Imposta `is_ambiguous=True` se `method == "content_alias_ambiguous"`

## Priorità di risoluzione

```
1. channels.yaml config (active=true, trader_id presente)
   ├── (chat_id, topic_id) → source_topic_config
   └── (chat_id, None)     → source_chat_id
2. EffectiveTraderResolver
   ├── content_alias
   ├── content_alias_ambiguous   → is_ambiguous=True
   ├── reply_chain
   ├── reply_chain_alias
   ├── source_chat_username
   └── source_chat_title
3. unresolved                    → trader_id=None
```
