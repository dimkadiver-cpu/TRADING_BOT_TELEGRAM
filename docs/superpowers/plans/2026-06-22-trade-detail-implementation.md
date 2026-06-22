# Trade Detail `/trade #n` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allineare il formatter `/trade #n` al design approvato: BE con prezzo esplicito, Actions per stato, link clean_log inline, SL visibile anche quando assente.

**Architecture:** Quattro fix chirurgici su due file esistenti (`trade_detail.py`, `status_queries.py`) + nuovi test in `test_readonly_formatters.py`. Nessuna migrazione DB — il filtraggio eventi è già implementato via `_EVENT_LABEL_MAP` in `status_queries.py` e corrisponde semanticamente al flag `is_main_event`.

**Tech Stack:** Python 3.12, pytest, sqlite3, Telegram MarkdownV2 / HTML (il formato link `→ [clean_log](url)` usa la sintassi già presente nel bot)

## Global Constraints

- Nessuna dipendenza nuova
- Non modificare la firma pubblica di `format_trade_detail(detail: TradeDetail | None) -> str`
- Non modificare la struttura di `TradeDetail` dataclass (solo `has_be` già presente)
- I test devono passare con `pytest tests/runtime_v2/control_plane/test_readonly_formatters.py`
- Non rompere test esistenti

---

## File Map

| File | Azione | Responsabilità |
|---|---|---|
| `src/runtime_v2/control_plane/status_queries.py` | Modifica | Aggiunge `be_protection_status` alla query `get_trade()`, setta `has_be` correttamente |
| `src/runtime_v2/control_plane/formatters/trade_detail.py` | Modifica | BE display, Actions matrix, SL sempre visibile, link format |
| `tests/runtime_v2/control_plane/test_readonly_formatters.py` | Modifica | Aggiunge test per tutti i nuovi comportamenti |

---

## Task 1: Fix `has_be` in `status_queries.get_trade()`

**Files:**
- Modify: `src/runtime_v2/control_plane/status_queries.py:547-685`
- Test: `tests/runtime_v2/control_plane/test_readonly_formatters.py`

**Interfaces:**
- Produces: `TradeDetail.has_be: bool` valorizzato correttamente (era sempre `False`)

- [ ] **Step 1: Scrivi il test failing**

In `tests/runtime_v2/control_plane/test_readonly_formatters.py`, aggiungi dopo `test_trade_detail_not_found`:

```python
def test_trade_detail_has_be_reflected_in_output():
    """has_be=True deve mostrare SL: — · BE: <price>, non SL: <price> · BE: No."""
    detail = _make_detail(
        state="OPEN",
        sl_price="63,500",
        has_be=True,
    )
    text = format_trade_detail(detail)
    assert "BE: 63,500" in text
    assert "SL:    —" in text
    assert "BE: No" not in text
```

- [ ] **Step 2: Esegui il test per verificare che fallisca**

```bash
pytest tests/runtime_v2/control_plane/test_readonly_formatters.py::test_trade_detail_has_be_reflected_in_output -v
```

Expected: FAIL — il formatter mostra `BE: set` e non `BE: 63,500`

- [ ] **Step 3: Aggiorna la query SQL in `status_queries.py`**

Trova il metodo `get_trade()` a riga ~547. Ci sono due branch della query (con e senza JOIN su `raw_messages`). In entrambi aggiungi `t.be_protection_status` come colonna aggiuntiva (indice 13):

Branch con JOIN (riga ~551):
```python
row = conn.execute(
    "SELECT t.trade_chain_id, t.symbol, t.side, t.trader_id, t.account_id, "
    "t.lifecycle_state, t.entry_avg_price, t.current_stop_price, "
    "t.management_plan_json, t.risk_snapshot_json, t.plan_state_json, "
    "COALESCE(t.source_chat_id, rm.source_chat_id), "
    "COALESCE(t.telegram_message_id, rm.telegram_message_id), "
    "t.be_protection_status "
    "FROM ops_trade_chains t "
    "LEFT JOIN raw_messages rm ON t.raw_message_id = rm.raw_message_id "
    "WHERE t.trade_chain_id=?",
    (chain_id,),
).fetchone()
```

Branch senza JOIN (riga ~563):
```python
row = conn.execute(
    "SELECT trade_chain_id, symbol, side, trader_id, account_id, "
    "lifecycle_state, entry_avg_price, current_stop_price, "
    "management_plan_json, risk_snapshot_json, plan_state_json, "
    "source_chat_id, telegram_message_id, "
    "be_protection_status "
    "FROM ops_trade_chains WHERE trade_chain_id=?",
    (chain_id,),
).fetchone()
```

- [ ] **Step 4: Aggiorna `has_be` nel costruttore `TradeDetail` a fine metodo (~riga 669)**

Trova il commento `has_be = False  # not populated`. Sostituisci con:

```python
has_be = (row[13] == "PROTECTED") if row[13] is not None else False
```

- [ ] **Step 5: Esegui il test per verificare che passi**

```bash
pytest tests/runtime_v2/control_plane/test_readonly_formatters.py::test_trade_detail_has_be_reflected_in_output -v
```

Expected: FAIL ancora — il formatter non usa ancora `has_be` correttamente (verrà fixato nel Task 2)

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/control_plane/status_queries.py
git add tests/runtime_v2/control_plane/test_readonly_formatters.py
git commit -m "fix: populate has_be in get_trade() from be_protection_status"
```

---

## Task 2: Fix SL/BE block nel formatter

**Files:**
- Modify: `src/runtime_v2/control_plane/formatters/trade_detail.py:87-97`
- Test: `tests/runtime_v2/control_plane/test_readonly_formatters.py`

**Interfaces:**
- Consumes: `p["has_be"]: bool`, `p["sl_price"]: str | None`
- Produces: riga SL sempre visibile, BE con prezzo esplicito quando attivo

- [ ] **Step 1: Aggiungi test per SL mancante (REVIEW_REQUIRED)**

In `test_readonly_formatters.py`, aggiungi:

```python
def test_trade_detail_sl_missing_shows_dash():
    """Quando sl_price è None, la riga SL deve mostrare '—'."""
    detail = _make_detail(
        state="REVIEW_REQUIRED",
        sl_price=None,
        has_be=False,
        is_actionable=True,
    )
    text = format_trade_detail(detail)
    assert "SL:    —" in text
    assert "BE: No" not in text
```

- [ ] **Step 2: Esegui i test failing**

```bash
pytest tests/runtime_v2/control_plane/test_readonly_formatters.py::test_trade_detail_sl_missing_shows_dash tests/runtime_v2/control_plane/test_readonly_formatters.py::test_trade_detail_has_be_reflected_in_output -v
```

Expected: entrambi FAIL

- [ ] **Step 3: Aggiorna il blocco SL/BE in `trade_detail.py`**

Sostituisci il `ConditionalBlock` SL (righe ~87-97):

```python
# 3c. SL / BE — sempre presente (SL: — se mancante, SL: — · BE: price se attivo)
DerivedBlock(
    text_fn=lambda p: (
        f"SL:    — · BE: {p['sl_price']}"
        if p.get("has_be")
        else (
            f"SL:    {p['sl_price']} · BE: No"
            if p.get("sl_price")
            else "SL:    —"
        )
    )
),
```

Rimuovi il `ConditionalBlock` wrapper che lo circondava (non serve più la condizione — la riga è sempre presente).

- [ ] **Step 4: Esegui i test**

```bash
pytest tests/runtime_v2/control_plane/test_readonly_formatters.py -v
```

Expected: `test_trade_detail_has_be_reflected_in_output` e `test_trade_detail_sl_missing_shows_dash` PASS. Nessuna regressione.

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/control_plane/formatters/trade_detail.py
git add tests/runtime_v2/control_plane/test_readonly_formatters.py
git commit -m "fix: SL/BE block shows price when BE active, dash when SL absent"
```

---

## Task 3: Actions matrix per stato

**Files:**
- Modify: `src/runtime_v2/control_plane/formatters/trade_detail.py:155-165`
- Test: `tests/runtime_v2/control_plane/test_readonly_formatters.py`

**Interfaces:**
- Consumes: `p["state"]: str`, `p["chain_id"]: int`, `p["is_actionable"]: bool`, `p["is_terminal"]: bool`
- Produces: Actions condizionali per stato secondo la matrice

Matrice da implementare:
- `WAITING_ENTRY` → `Actions: /cancel_n`
- `OPEN` / `PARTIALLY_CLOSED` → `Actions: /cancel_n · /close_n`
- `REVIEW_REQUIRED` → `Actions: /close_n`
- stati terminali / non azionabili → sezione assente

- [ ] **Step 1: Aggiungi test per WAITING_ENTRY e REVIEW_REQUIRED**

```python
def test_trade_detail_actions_waiting_entry_only_cancel():
    """WAITING_ENTRY deve mostrare solo /cancel_n, non /close_n."""
    detail = _make_detail(
        state="WAITING_ENTRY",
        is_actionable=True, is_terminal=False,
        unrealized_pnl=None, cum_realized_pnl=None,
    )
    text = format_trade_detail(detail)
    assert "/cancel_5" in text
    assert "/close_5" not in text


def test_trade_detail_actions_review_required_only_close():
    """REVIEW_REQUIRED deve mostrare solo /close_n, non /cancel_n."""
    detail = _make_detail(
        state="REVIEW_REQUIRED",
        is_actionable=True, is_terminal=False,
        sl_price=None,
    )
    text = format_trade_detail(detail)
    assert "/close_5" in text
    assert "/cancel_5" not in text


def test_trade_detail_actions_open_has_both():
    """OPEN deve mostrare /cancel_n · /close_n."""
    detail = _make_detail(state="OPEN", is_actionable=True, is_terminal=False)
    text = format_trade_detail(detail)
    assert "/cancel_5" in text
    assert "/close_5" in text
```

- [ ] **Step 2: Esegui i test failing**

```bash
pytest tests/runtime_v2/control_plane/test_readonly_formatters.py::test_trade_detail_actions_waiting_entry_only_cancel tests/runtime_v2/control_plane/test_readonly_formatters.py::test_trade_detail_actions_review_required_only_close -v
```

Expected: entrambi FAIL — il formatter attuale mostra sempre `/cancel_n · /close_n`

- [ ] **Step 3: Sostituisci il blocco Actions in `trade_detail.py`**

Sostituisci il `ConditionalBlock` Actions (righe ~155-165) con:

```python
# 5. Actions — matrice per stato
ConditionalBlock(
    condition=lambda p: bool(p.get("is_actionable")) and not p.get("is_terminal"),
    blocks=[
        SeparatorBlock(),
        DerivedBlock(
            text_fn=lambda p: _fmt_actions(p["state"], p["chain_id"])
        ),
    ],
),
```

Aggiungi la funzione `_fmt_actions` prima di `_TRADE_DETAIL_BLOCKS`:

```python
def _fmt_actions(state: str, chain_id: int) -> str:
    if state == "WAITING_ENTRY":
        return f"Actions: /cancel_{chain_id}"
    if state == "REVIEW_REQUIRED":
        return f"Actions: /close_{chain_id}"
    # OPEN, PARTIALLY_CLOSED e altri stati azionabili
    return f"Actions: /cancel_{chain_id} · /close_{chain_id}"
```

- [ ] **Step 4: Esegui tutti i test**

```bash
pytest tests/runtime_v2/control_plane/test_readonly_formatters.py -v
```

Expected: tutti PASS, nessuna regressione

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/control_plane/formatters/trade_detail.py
git add tests/runtime_v2/control_plane/test_readonly_formatters.py
git commit -m "fix: actions matrix per stato — WAITING_ENTRY=cancel only, REVIEW_REQUIRED=close only"
```

---

## Task 4: Link clean_log inline nel formatter eventi

**Files:**
- Modify: `src/runtime_v2/control_plane/formatters/trade_detail.py:33-49`
- Test: `tests/runtime_v2/control_plane/test_readonly_formatters.py`

**Interfaces:**
- Consumes: `ev["clean_log_link"]: str | None`, `ev["source"]: str | None`
- Produces: `Source: Signal → [clean_log](url)` quando link presente, `Source: Signal` senza link quando assente

- [ ] **Step 1: Aggiungi test per il formato link**

```python
def test_trade_detail_event_link_inline_format():
    """clean_log_link deve apparire come link Markdown inline → [clean_log](url)."""
    detail = _make_detail(events=[
        TradeEvent(
            label="SIGNAL ACCEPTED", timestamp="14 Jun 09:10:00",
            source="Signal", event_type=None, reason=None,
            clean_log_link="https://t.me/c/123456/789",
        ),
    ])
    text = format_trade_detail(detail)
    assert "→ [clean_log](https://t.me/c/123456/789)" in text


def test_trade_detail_event_no_link_shows_source_only():
    """Senza clean_log_link, deve mostrare solo 'Source: Signal' senza freccia."""
    detail = _make_detail(events=[
        TradeEvent(
            label="SIGNAL ACCEPTED", timestamp="14 Jun 09:10:00",
            source="Signal", event_type=None, reason=None,
            clean_log_link=None,
        ),
    ])
    text = format_trade_detail(detail)
    assert "Source: Signal" in text
    assert "→" not in text
    assert "clean_log" not in text
```

- [ ] **Step 2: Esegui i test failing**

```bash
pytest tests/runtime_v2/control_plane/test_readonly_formatters.py::test_trade_detail_event_link_inline_format tests/runtime_v2/control_plane/test_readonly_formatters.py::test_trade_detail_event_no_link_shows_source_only -v
```

Expected: `test_trade_detail_event_link_inline_format` FAIL (formato attuale usa `->` non `→ [clean_log]`)

- [ ] **Step 3: Aggiorna `_render_event` in `trade_detail.py`**

Sostituisci la funzione `_render_event` (righe ~33-49):

```python
def _render_event(ev: dict, i: int, p: dict) -> list[str]:
    label = ev.get("label", "EVENT")
    ts = ev.get("timestamp", "")
    lines = [""] if i > 0 else []
    lines.append(f"• {label} · {ts}")
    if ev.get("event_type"):
        lines.append(f"  Type: {ev['event_type']}")
    if ev.get("reason"):
        lines.append(f"  Reason: {ev['reason']}")
    source = ev.get("source")
    link = ev.get("clean_log_link")
    if source:
        if link:
            lines.append(f"  Source: {source} → [clean_log]({link})")
        else:
            lines.append(f"  Source: {source}")
    return lines
```

- [ ] **Step 4: Esegui tutti i test**

```bash
pytest tests/runtime_v2/control_plane/test_readonly_formatters.py -v
```

Expected: tutti PASS

- [ ] **Step 5: Esegui la suite completa per rilevare regressioni**

```bash
pytest tests/runtime_v2/control_plane/ -v
```

Expected: tutti PASS

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/control_plane/formatters/trade_detail.py
git add tests/runtime_v2/control_plane/test_readonly_formatters.py
git commit -m "fix: event clean_log link formato inline Telegram '→ [clean_log](url)'"
```

---

## Self-Review

**Copertura spec:**
- ✅ Struttura 6 sezioni: già esistente, non modificata
- ✅ BE con prezzo esplicito `SL: — · BE: <prezzo>`: Task 1 + 2
- ✅ SL visibile anche quando assente (`SL: —`): Task 2
- ✅ Actions matrice per stato: Task 3
- ✅ Link clean_log inline `→ [clean_log](url)`: Task 4
- ✅ Timeline filtra solo eventi principali: già implementato via `_EVENT_LABEL_MAP` in `status_queries.py`
- ✅ Fallback senza link: Task 4

**Placeholder scan:** nessuno — tutti i task hanno codice completo.

**Type consistency:** `sl_price: str | None` usato in Task 2 è già il tipo in `TradeDetail`. `has_be: bool` già in `TradeDetail`. `state: str` già in payload. `chain_id: int` già in payload.
