# intake — Funzionalità

## Responsabilità

Il package `intake` gestisce la prima fase della pipeline: riceve un `RawIngestItem` dal listener e produce un `ParserDispatchCandidate`, oppure `None` se il messaggio non può procedere.

## Componenti

### `models.py`

Definisce i contratti dati dell'intake:

- **`RawIngestItem`** — dataclass frozen. Rappresenta l'evento grezzo ricevuto dal listener prima della persistenza. Campi principali: `source_chat_id`, `telegram_message_id`, `raw_text`, `has_media`, `acquisition_mode` (`live` | `catchup` | `import`).

- **`RawMessageEnvelope`** — Pydantic model. Rappresenta il messaggio già persistito in DB. Aggiunge `raw_message_id`, `acquired_at`, `acquisition_status` (immutabile), `processing_status` (mutabile), `resolved_trader_id`.

- **`IntakeConfig`** — dataclass frozen. Configurazione globale pipeline. Campo: `reply_chain_depth_limit` (default 5) — contratto dichiarato per il limite reply-chain (l'enforcement su `EffectiveTraderResolver` è pending).

- **`AcquisitionStatus`** — `Literal["ACQUIRED", "BLACKLISTED", "MEDIA_ONLY_SKIPPED"]`. Impostato una sola volta, mai modificato.

- **`ProcessingStatusV2`** — `Literal["pending", "processing", "done", "failed", "blacklisted", "review", "skipped"]`. Traccia solo la fase intake; il parser ha la propria tabella di stato.

---

### `eligibility.py`

- **`IntakeEligibilityCheck`** — wrapper su `MessageEligibilityEvaluator` (legacy). Riceve un `RawMessageEnvelope` e restituisce un `EligibilityOutcome`.

- **`EligibilityOutcome`** — dataclass frozen con `eligible: bool` e `review_reason: str | None`.

  Caso principale di ineleggibilità: messaggio breve senza link forte al trader (short update senza reply o link esplicito). Questi messaggi finiscono in `processing_status=review` per revisione manuale.

---

### `processor.py`

- **`RuntimeV2IntakeProcessor`** — orchestratore principale. Dipendenze iniettate nel costruttore: `repo`, `eligibility`, `resolver`, `channel_config`, `config`.

  **Metodo pubblico:** `process(item: RawIngestItem) -> ParserDispatchCandidate | None`

  **Pipeline interna (13 step):**
  1. Salva raw (dedup idempotente)
  2. Blacklist globale → `BLACKLISTED`, `None`
  3. Media-only senza testo → `MEDIA_ONLY_SKIPPED`, `None`
  4. Eligibility → `review`, `None`
  5. Marca `processing`
  6. Risolve trader
  7. Ambiguo o non risolto → `review`, `None`
  8. Persiste risoluzione
  9. Deriva `parser_profile`
  10. Valida profilo in registry parser_v2 → `review`, `None`
  11. Costruisce `ParserContext`
  12. Marca `done`
  13. Ritorna `ParserDispatchCandidate`

## Invarianti

- `acquisition_status` è impostato una volta sola (al passo 1, 2 o 3) e non cambia mai.
- `processing_status=done` significa: trader risolto, `ParserDispatchCandidate` prodotto, pronto per il parser.
- `processing_status=review` significa: messaggio in coda di revisione manuale, `acquisition_status` rimane `ACQUIRED`.
