# Piano A — Observability + Dashboard

> Sostituisce la combinazione di:
> - `2026-06-19-commands-scope-read-only.md`
> - `2026-06-19-dashboard.md`

**Spec madre di riferimento:** `docs/superpowers/specs/2026-06-19-commands-stats-dashboard-design.md`

**Goal:** implementare il lato read-only del control plane in modo coerente: scope per account/trader, `/trades`, `/pnl`, `/stats`, `/status`, `/control`, `/reviews`, e dashboard inline con auto-refresh sulla vista corrente.

**Architecture:**
- `ScopeResolver` risolve `(chat_id, thread_id) -> QueryScope`.
- `StatusQueries` diventa il read-model scoped per tutti i comandi non distruttivi.
- `DashboardManager` legge lo stesso read-model e aggiorna i messaggi dashboard dopo ogni `CLEAN_LOG` inviato.
- La PnL aperta è calcolata da DB, non letta direttamente dall'exchange in tempo reale.

**Non-goal:**
- nessun comando distruttivo;
- nessuna conferma inline emergency;
- nessun polling UI dedicato.

---

## Drift già fissati

1. `closed_at` non esiste: usare fallback a `updated_at`.
2. `review_reason` non esiste: motivo review da `ops_lifecycle_events.payload_json`.
3. Auto-refresh dashboard: trigger dopo `CLEAN_LOG` inviato, non su raw exchange event.
4. PnL aperta: formula per-chain con `ops_market_snapshots`, non `unrealizedPnl` live.
5. Freshness snapshot: `position_reconciliation_interval_seconds + max(30s, interval * 0.25)`.
6. Migration dashboard: in `db/ops_migrations`, non sotto `src/runtime_v2/control_plane/`.

---

## Deliverable

1. `QueryScope` + `ScopeResolver`.
2. Query scoped per tutti i comandi read-only.
3. Formatter commands block-based coerenti con la spec madre.
4. Dashboard inline con 5 viste e paginazione.
5. Auto-refresh della vista corrente, senza reset tab/pagina.
6. Test mirati su scope, query, formatter, dashboard manager.

---

## Task 1 — Scope Foundation

**Files:**
- `src/runtime_v2/control_plane/scope_resolver.py`
- `src/runtime_v2/control_plane/auth.py`
- `src/runtime_v2/control_plane/telegram_bot.py`
- test dedicati in `tests/runtime_v2/control_plane/`

**Work:**
- introdurre `QueryScope(account_id, trader_ids)`;
- mappare topic `commands` e `clean_log` a scope;
- lasciare `tech_log` fuori dallo scope comandi;
- fare reply sempre nel `chat_id` / `message_thread_id` di origine.

**Acceptance:**
- un comando inviato nel topic commands dell’account A non può rispondere nel topic commands del default account;
- un topic `clean_log` per trader produce scope ristretto a quel trader;
- `/dashboard` è l’unico comando consentito da `clean_log`.

---

## Task 2 — Read Model Scoped

**Files:**
- `src/runtime_v2/control_plane/status_queries.py`
- `src/runtime_v2/control_plane/service.py`

**Work:**
- aggiungere scope a `get_status`, `get_open_trades`, `get_control`, `get_reviews`, `get_pnl`, `get_stats`;
- lasciare `/health` globale;
- aggiungere query dashboard-specifiche per Attivi, Chiusi, Bloccati, PnL, Stats;
- usare lo schema reale applicando le migration nei test.

**PnL aperta:**
- formula: `(mark_price - entry_avg_price) * open_position_qty * direction`;
- `mark_price` da ultimo `ops_market_snapshots(account_id, symbol)`;
- `PnL: —` se manca `mark_price`, `entry_avg_price` o `open_position_qty`;
- mostrare sempre `HH:MM:SS` dello snapshot e l’età relativa.

**Nota tecnica:**
- per rendere affidabile la PnL aperta, l’implementazione deve anche agganciare l’aggiornamento di `ops_market_snapshots` alla position reconciliation già esistente, non introdurre un polling separato per il dashboard.

---

## Task 3 — Commands Formatting

**Files:**
- `src/runtime_v2/control_plane/formatters/_blocks.py`
- `src/runtime_v2/control_plane/formatters/trades.py`
- `src/runtime_v2/control_plane/formatters/pnl.py`
- `src/runtime_v2/control_plane/formatters/status.py`
- `src/runtime_v2/control_plane/formatters/control.py`
- `src/runtime_v2/control_plane/formatters/stats.py`
- `src/runtime_v2/control_plane/formatters/templates/commands.py`

**Work:**
- estendere i blocchi condivisi dove serve (`SectionBlock` callable, `TableBlock`);
- rendere i formatter wrapper sottili su `render_template(...)`;
- allineare header, scope label, warning snapshot, tabella stats.

**Acceptance:**
- `/trades` mostra entry, SL/BE, qty, PnL e snapshot time;
- `/pnl` mostra snapshot account e realized PnL coerente;
- `/stats` usa la stessa logica di bucketing della spec madre.

---

## Task 4 — Dashboard Data + Templates

**Files:**
- `src/runtime_v2/control_plane/formatters/dashboard.py`
- `src/runtime_v2/control_plane/formatters/templates/dashboard.py`
- `src/runtime_v2/control_plane/status_queries.py`

**Work:**
- implementare le 5 viste: `attivi`, `chiusi`, `bloccati`, `pnl`, `stats`;
- paginazione 5 item per pagina per Attivi/Chiusi/Bloccati;
- keyboard con `noop`, prev/next, refresh;
- vista Attivi usa la stessa PnL aperta calcolata del read-model.

**Acceptance:**
- il dashboard non inventa dati diversi dai comandi read-only;
- Chiusi usa `updated_at` se `closed_at` non esiste;
- Bloccati unisce `REVIEW_REQUIRED` e command `FAILED`.

---

## Task 5 — Dashboard Manager + Auto-refresh

**Files:**
- `db/ops_migrations/NNN_ops_dashboard_messages.sql`
- `src/runtime_v2/control_plane/dashboard_manager.py`
- `src/runtime_v2/control_plane/notification_dispatcher.py`
- `src/runtime_v2/control_plane/bootstrap.py`
- `src/runtime_v2/control_plane/telegram_bot.py`

**Work:**
- creare tabella `ops_dashboard_messages`;
- creare/aggiornare il dashboard per topic;
- salvare `current_view`;
- refresh dopo ogni `CLEAN_LOG` inviato con callback dispatcher;
- throttling 5s per messaggio;
- mantenere la vista corrente: nessun cambio tab automatico.

**Acceptance:**
- se il dashboard è su Attivi, resta su Attivi dopo refresh;
- se una chain passa a CLOSED, sparisce da Attivi ma non apre Chiusi da sola;
- nessun reset pagina su refresh automatico;
- `MessageNotModified` gestito silenziosamente.

---

## Task 6 — Verification

**Cheap gates first:**
- test scope resolver;
- test auth/control-plane routing;
- test formatter commands;
- test dashboard templates;
- test dashboard manager;
- test query scoped con DB temporaneo creato da `db/ops_migrations/*.sql`.

**Primary signal:**
- dashboard e comandi read-only mostrano dati coerenti col DB scoped per topic/account/trader.

**Secondary signals:**
- `pytest tests/runtime_v2/control_plane/ -v --tb=short`

---

## Out of Scope

- `/close_all`, `/close`, `/cancel_all`
- callback confirm/cancel
- ownership di `CANCEL_PENDING_ENTRY`
- audit e idempotency dei comandi emergency

Questi restano nel piano separato `2026-06-19-commands-emergency-close.md`.

---

## Suggested Sequence

1. Scope foundation
2. Read model scoped
3. Commands formatting
4. Dashboard data/templates
5. Dashboard manager + wiring
6. Verification finale

---

## Completion Gate

Il piano è completo quando:
- la spec madre resta unica fonte di prodotto;
- questo file copre tutto il read-side control plane;
- il piano emergency resta separato e non dipende da dettagli dashboard, salvo `QueryScope`.
