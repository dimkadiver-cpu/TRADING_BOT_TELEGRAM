# Architettura Block-Based — Commands

Tutti i messaggi comandi usano `render_template(blocks, payload)` — stesso sistema di
clean_log e tech_log. Il block system genera SOLO testo. La InlineKeyboardMarkup
(conferme, dashboard) è aggiunta separatamente da TelegramControlBot.

---

## Estensioni a `_blocks.py`

### Nuovo blocco: TableBlock

Serve per la tabella stats (4 righe × 4 colonne allineate).
Non esiste nei blocchi attuali — va aggiunto.

```python
@dataclass
class TableBlock:
    rows_key: str                             # chiave nel payload → list[dict]
    columns: list[tuple[str, str, int, Callable]]  # (header, key, min_width, fmt)
    show_header: bool = True
    fallback: str = "—"
```

Calcola la larghezza max per colonna, allinea con rjust/ljust.

### Estensione SectionBlock

`SectionBlock.label` esteso da `str` a `str | Callable[[dict], str]`, coerente con `FieldBlock.label`.
Il renderer chiama `lbl(p)` se callable.

---

## Helper _cmd_header() (come _tech_header in tech_log)

```python
def _cmd_header(emoji: str, command: str) -> list:
    return [
        DerivedBlock(text_fn=lambda p, _e=emoji, _c=command:
            f"{_e} {_c} — {p['account_id']}"
            + (f" · {p['trader_id']}" if p.get("trader_id") else "")
        ),
        SeparatorBlock(),
    ]
```

Produce:  `📊 TRADES — demo_1`  oppure  `📊 TRADES — demo_1 · trader_a`

---

## /trades — struttura blocchi

```python
_TRADES = TemplateConfig(
    blocks=[
        *_cmd_header("📊", "TRADES"),
        DerivedBlock(lambda p:
            f"Updated: {p['updated_at']}  |  Snapshot: {p['snapshot_age']}"
        ),
        StaticBlock(""),
        BranchBlock(
            condition=lambda p: bool(p.get("rows")),
            then_blocks=[
                ListBlock("rows", _render_trade_row),
            ],
            else_blocks=[StaticBlock("Nessun trade aperto.")],
        ),
        ConditionalBlock(
            condition=lambda p: p.get("snapshot_stale", False),
            blocks=[
                StaticBlock(""),
                StaticBlock("⚠️ Snapshot >120s — PnL non aggiornato"),
            ],
        ),
        SeparatorBlock(),
        StaticBlock("/trade #id  ·  /close <symbol>  ·  /cancel_all"),
    ],
)

def _render_trade_row(row: dict, i: int, p: dict) -> list[str]:
    side_emoji = "📈" if row["side"] == "LONG" else "📉"
    be = "  BE: ✓" if row.get("has_be") else ""
    sl = f"  SL: {row['sl_price']}" if row.get("sl_price") else ""
    pnl = row.get("pnl")
    pnl_str = f"+{pnl:.2f} USDT" if pnl and pnl >= 0 else (f"{pnl:.2f} USDT" if pnl else "—")
    return [
        f"#{row['chain_id']}  {side_emoji} {row['symbol']}   {row['side']}    {row['state']}",
        f"    Entry: {row.get('entry_price', '—')}{sl}{be}",
        f"    Qty: {row.get('qty', '—')}  |  PnL: {pnl_str}",
        "",
    ]
```

Payload keys: `account_id`, `trader_id?`, `updated_at`, `snapshot_age`, `snapshot_stale`,
`rows: list[dict(chain_id, symbol, side, state, entry_price, sl_price, has_be, qty, pnl)]`

---

## /pnl — struttura blocchi

```python
_PNL = TemplateConfig(
    blocks=[
        *_cmd_header("💰", "PNL"),
        DerivedBlock(lambda p:
            f"Account: {p.get('account_id', 'n/a')}  |  {p['updated_at']}"
        ),
        StaticBlock(""),
        SectionBlock("Snapshot account:", [
            FieldBlock("  Equity",   key="equity",   fmt=money, optional=False, default="n/a"),
            FieldBlock("  Balance",  key="balance",  fmt=money, optional=False, default="n/a"),
            FieldBlock("  Margin",   key="margin",   fmt=money, optional=False, default="n/a"),
        ]),
        StaticBlock(""),
        BranchBlock(
            condition=lambda p: p.get("has_realized"),
            then_blocks=[
                SectionBlock("Realizzato (trade chiusi):", [
                    FieldBlock("  Gross PnL", key="gross_pnl", fmt=money_signed),
                    FieldBlock("  Fees",      key="fees",      fmt=money),
                    FieldBlock("  Funding",   key="funding",   fmt=money),
                    SeparatorBlock(),
                    FieldBlock("  Netto",     key="net_pnl",   fmt=money_signed, optional=False, default="n/a"),
                ]),
            ],
            else_blocks=[StaticBlock("Realizzato (trade chiusi):\n  Nessun trade chiuso.")],
        ),
        StaticBlock(""),
        DerivedBlock(lambda p:
            f"Open: {p['open_count']}  |  Partial: {p['partial_count']}  |  Waiting: {p['waiting_count']}"
        ),
    ],
)
```

Payload keys: `account_id`, `updated_at`, `equity`, `balance`, `margin`,
`has_realized`, `gross_pnl`, `fees`, `funding`, `net_pnl`,
`open_count`, `partial_count`, `waiting_count`, `trader_id?`

---

## /stats — struttura blocchi (usa TableBlock)

```python
_STATS = TemplateConfig(
    blocks=[
        *_cmd_header("📈", "STATS"),
        BranchBlock(
            condition=lambda p: bool(p.get("periods")),
            then_blocks=[
                TableBlock(
                    rows_key="periods",
                    columns=[
                        ("",          "label",   10, text),
                        ("Trades",    "trades",   6, num),
                        ("Win%",      "win_pct",  5, _fmt_win_pct),
                        ("PnL netto", "pnl_net", 12, money_signed),
                        ("Fees",      "fees",     9, money),
                    ],
                ),
                SeparatorBlock(),
                DerivedBlock(lambda p:
                    f"Best:   #{p['best_id']}  {p['best_symbol']}  {money_signed(p['best_pnl'])}"
                    if p.get("best_id") else ""
                ),
                DerivedBlock(lambda p:
                    f"Worst:  #{p['worst_id']}  {p['worst_symbol']}  {money_signed(p['worst_pnl'])}"
                    if p.get("worst_id") else ""
                ),
                ConditionalBlock(
                    condition=lambda p: not p.get("trader_id"),
                    blocks=[
                        SeparatorBlock(),
                        StaticBlock("/stats trader_a  per filtrare per trader"),
                    ],
                ),
            ],
            else_blocks=[StaticBlock("Nessun trade chiuso — statistiche non disponibili.")],
        ),
    ],
)
```

Payload keys: `account_id`, `trader_id?`,
`periods: list[dict(label, trades, win_pct, pnl_net, fees)]` (4 elementi: Oggi/7g/30g/Tot),
`best_id`, `best_symbol`, `best_pnl`, `worst_id`, `worst_symbol`, `worst_pnl`

---

## Emergency — /close_all

### _CLOSE_ALL_PREVIEW (testo + keyboard aggiunta da TelegramControlBot)

```python
_CLOSE_ALL_PREVIEW = TemplateConfig(
    blocks=[
        *_cmd_header("🚨", "CLOSE ALL"),
        DerivedBlock(lambda p: f"Posizioni da chiudere: {len(p.get('chains', []))}"),
        StaticBlock(""),
        ListBlock("chains", _render_chain_preview_row),
        StaticBlock(""),
        StaticBlock("⚠️ Verranno inviati ordini MARKET di chiusura."),
        StaticBlock(""),
        StaticBlock("Confermi?"),
        # keyboard [✅ Conferma] [❌ Annulla] aggiunta da TelegramControlBot
    ],
)

_CLOSE_ALL_RESULT_OK = TemplateConfig(
    blocks=[
        *_cmd_header("🚨", "CLOSE ALL"),
        ListBlock("chains", _render_chain_preview_row),
        StaticBlock(""),
        DerivedBlock(lambda p: f"✅ ESEGUITO — {p['executed_at']}"),
        DerivedBlock(lambda p: f"{p['command_count']} comandi MARKET_CLOSE inseriti."),
        StaticBlock("⚡ Monitorare con /trades"),
    ],
)

_CLOSE_ALL_RESULT_CANCELLED = TemplateConfig(
    blocks=[
        *_cmd_header("🚨", "CLOSE ALL"),
        ListBlock("chains", _render_chain_preview_row),
        StaticBlock(""),
        DerivedBlock(lambda p: f"❌ ANNULLATO — {p['cancelled_at']}"),
        StaticBlock("Nessuna azione eseguita."),
    ],
)

def _render_chain_preview_row(chain: dict, i: int, p: dict) -> list[str]:
    side_emoji = "📈" if chain["side"] == "LONG" else "📉"
    return [f"#{chain['chain_id']}  {side_emoji} {chain['symbol']}   {chain['side']}    {chain['state']}"]
```

---

## Emergency — /close (singolo symbol)

```python
_CLOSE_SINGLE_PREVIEW = TemplateConfig(
    blocks=[
        *_cmd_header("🚨", "CLOSE"),
        BranchBlock(
            condition=lambda p: len(p.get("chains", [])) > 1,
            then_blocks=[
                DerivedBlock(lambda p: f"Trovate {len(p['chains'])} posizioni su {p['symbol']}:"),
                StaticBlock(""),
                ListBlock("chains", _render_chain_preview_row_with_entry),
                StaticBlock(""),
                StaticBlock("⚠️ Verranno chiuse entrambe."),
            ],
            else_blocks=[
                StaticBlock("Posizione da chiudere:"),
                StaticBlock(""),
                ListBlock("chains", _render_chain_preview_row_with_entry),
                StaticBlock(""),
                StaticBlock("⚠️ Verrà inviato un ordine MARKET di chiusura."),
            ],
        ),
        StaticBlock(""),
        StaticBlock("Confermi?"),
        # keyboard [✅ Conferma] [❌ Annulla] aggiunta da TelegramControlBot
    ],
)

_CLOSE_SINGLE_RESULT_OK = TemplateConfig(
    blocks=[
        *_cmd_header("🚨", "CLOSE"),
        ListBlock("chains", _render_chain_preview_row),
        StaticBlock(""),
        DerivedBlock(lambda p: f"✅ ESEGUITO — {p['executed_at']}"),
        DerivedBlock(lambda p: f"{p['command_count']} comando MARKET_CLOSE inserito."),
        DerivedBlock(lambda p: f"⚡ Monitorare con /trade #{p['chains'][0]['chain_id']}"
                    if len(p.get('chains', [])) == 1 else "⚡ Monitorare con /trades"),
    ],
)

_CLOSE_SINGLE_RESULT_CANCELLED = TemplateConfig(
    blocks=[
        *_cmd_header("🚨", "CLOSE"),
        ListBlock("chains", _render_chain_preview_row),
        StaticBlock(""),
        DerivedBlock(lambda p: f"❌ ANNULLATO — {p['cancelled_at']}"),
    ],
)

def _render_chain_preview_row_with_entry(chain: dict, i: int, p: dict) -> list[str]:
    side_emoji = "📈" if chain["side"] == "LONG" else "📉"
    pnl = chain.get("pnl")
    pnl_str = f"+{pnl:.2f} USDT" if pnl and pnl >= 0 else (f"{pnl:.2f} USDT" if pnl else "—")
    return [
        f"#{chain['chain_id']}  {side_emoji} {chain['symbol']}   {chain['side']}    {chain['state']}",
        f"    Entry: {chain.get('entry_price', '—')}  |  PnL: {pnl_str}",
    ]
```

---

## Emergency — /cancel_all

```python
_CANCEL_PREVIEW = TemplateConfig(
    blocks=[
        *_cmd_header("🛑", "CANCEL ALL"),
        DerivedBlock(lambda p: f"Ordini entry in attesa: {len(p.get('waiting', []))}"),
        StaticBlock(""),
        ListBlock("waiting", _render_chain_preview_row),
        StaticBlock(""),
        DerivedBlock(lambda p: f"Posizioni aperte non toccate: {p.get('open_count', 0)}"),
        StaticBlock(""),
        StaticBlock("Confermi la cancellazione?"),
        # keyboard [✅ Conferma] [❌ Annulla] aggiunta da TelegramControlBot
    ],
)

_CANCEL_RESULT_OK = TemplateConfig(
    blocks=[
        *_cmd_header("🛑", "CANCEL ALL"),
        ListBlock("waiting", _render_chain_preview_row),
        StaticBlock(""),
        DerivedBlock(lambda p: f"✅ ESEGUITO — {p['executed_at']}"),
        DerivedBlock(lambda p: f"{p['command_count']} ordini WAITING_ENTRY cancellati."),
        DerivedBlock(lambda p: f"Posizioni aperte non toccate: {p.get('open_count', 0)}"),
        StaticBlock("/trades per verificare."),
    ],
)

_CANCEL_RESULT_CANCELLED = TemplateConfig(
    blocks=[
        *_cmd_header("🛑", "CANCEL ALL"),
        ListBlock("waiting", _render_chain_preview_row),
        StaticBlock(""),
        DerivedBlock(lambda p: f"❌ ANNULLATO — {p['cancelled_at']}"),
    ],
)
```

---

## Dashboard — struttura blocchi

Viste: `dashboard_attivi`, `dashboard_chiusi`, `dashboard_bloccati`, `dashboard_pnl`, `dashboard_stats`.
La keyboard è sempre aggiunta da TelegramControlBot dopo render_template.
La riga paginazione `[← Prec] [Pagina N/M] [Succ →]` è aggiunta da `DashboardManager._render_view()`
solo se il totale trade supera 5 (soglia paginazione).

```python
_DASHBOARD_ATTIVI = TemplateConfig(
    blocks=[
        *_cmd_header("📊", "DASHBOARD — ATTIVI"),
        DerivedBlock(lambda p:
            f"{p['updated_at']}  |  Snapshot: {p['snapshot_age']}"
            + ("  ⚠️" if p.get("snapshot_stale") else "")
        ),
        StaticBlock(""),
        BranchBlock(
            condition=lambda p: bool(p.get("rows")),
            then_blocks=[ListBlock("rows", _render_trade_row_dashboard)],
            else_blocks=[StaticBlock("Nessun trade attivo.")],
        ),
    ],
)

_DASHBOARD_CHIUSI = TemplateConfig(
    blocks=[
        *_cmd_header("✅", "DASHBOARD — CHIUSI"),
        DerivedBlock(lambda p: p['updated_at']),
        StaticBlock(""),
        BranchBlock(
            condition=lambda p: bool(p.get("rows")),
            then_blocks=[ListBlock("rows", _render_closed_row_dashboard)],
            else_blocks=[StaticBlock("Nessun trade chiuso.")],
        ),
    ],
)

_DASHBOARD_BLOCCATI = TemplateConfig(
    blocks=[
        *_cmd_header("🚫", "DASHBOARD — BLOCCATI"),
        DerivedBlock(lambda p: p['updated_at']),
        StaticBlock(""),
        BranchBlock(
            condition=lambda p: bool(p.get("rows")),
            then_blocks=[ListBlock("rows", _render_blocked_row_dashboard)],
            else_blocks=[StaticBlock("Nessun trade bloccato.")],
        ),
    ],
)

def _fmt_price_leg(price: str, status: str) -> str:
    """status: 'filled' → price✓ | 'cancelled' → price✗ | 'pending' → price"""
    if status == "filled":
        return f"{price}✓"
    if status == "cancelled":
        return f"{price}✗"
    return str(price)

def _render_trade_row_dashboard(row: dict, i: int, p: dict) -> list[str]:
    # symbol via display_symbol(), no side emoji
    trader_tag = f"  [{row['trader_id']}]" if p.get("show_trader_tag") else ""
    lines = [f"#{row['chain_id']}  {row['symbol']}   {row['side']}   {row['state']}{trader_tag}"]

    # Entry legs: price✓ · price✗ · price
    entries = row.get("entries", [])
    if entries:
        entry_str = " · ".join(_fmt_price_leg(e["price"], e["status"]) for e in entries)
        lines.append(f"    Entry: {entry_str}")

    # TP levels: price✓ · price
    tps = row.get("tps", [])
    if tps:
        tp_str = " · ".join(_fmt_price_leg(t["price"], t["status"]) for t in tps)
        lines.append(f"    TP: {tp_str}")

    # SL + BE
    sl_be = f"    SL: {row['sl_price']}" if row.get("sl_price") else ""
    if row.get("has_be"):
        sl_be += "  BE: ✓"
    if sl_be:
        lines.append(sl_be)

    # PnL o waiting message
    if row["state"] == "WAITING_ENTRY":
        lines.append("    In attesa di riempimento")
    else:
        pnl = row.get("pnl")
        pnl_str = f"+{pnl:.2f} USDT" if pnl and pnl >= 0 else (f"{pnl:.2f} USDT" if pnl else "—")
        lines.append(f"    PnL: {pnl_str}")

    if row.get("signal_link"):
        lines.append(f"    {row['signal_link']}")
    lines.append("")
    return lines

def _render_closed_row_dashboard(row: dict, i: int, p: dict) -> list[str]:
    trader_tag = f"  [{row['trader_id']}]" if p.get("show_trader_tag") else ""
    pnl = row.get("pnl")
    pnl_str = f"+{pnl:.2f} USDT" if pnl and pnl >= 0 else (f"{pnl:.2f} USDT" if pnl else "—")
    lines = [f"#{row['chain_id']}  {row['symbol']}   {row['side']}   CLOSED{trader_tag}"]
    lines.append(f"     Opened: {row.get('opened_at', '—')}")
    if row.get("signal_link"):
        lines.append(f"     {row['signal_link']}")
    lines.append(_SEP)
    lines.append(f"     Closed: {row.get('closed_at', '—')}")
    if row.get("close_link"):
        lines.append(f"     {row['close_link']}")
    lines.append(_SEP)
    lines.append(f"     PnL: {pnl_str}   ⏱ {row.get('duration', '—')}")
    lines.append("")
    return lines

def _render_blocked_row_dashboard(row: dict, i: int, p: dict) -> list[str]:
    trader_tag = f"  [{row['trader_id']}]" if p.get("show_trader_tag") else ""
    lines = [f"#{row['chain_id']}  {row['symbol']}   {row['side']}   {row['block_type']}{trader_tag}"]
    lines.append(f"     Motivo: {row.get('reason', '—')}")
    lines.append(f"     {row.get('blocked_at', '—')}")
    if row.get("link"):
        lines.append(f"     {row['link']}")
    lines.append("")
    return lines

_DASHBOARD_PNL = TemplateConfig(
    blocks=[
        *_cmd_header("💰", "DASHBOARD — PNL"),
        DerivedBlock(lambda p: p['updated_at']),
        StaticBlock(""),
        SectionBlock("Account:", [
            FieldBlock("  Equity",  key="equity",  fmt=money, optional=False, default="n/a"),
            FieldBlock("  Balance", key="balance", fmt=money, optional=False, default="n/a"),
            FieldBlock("  Margin",  key="margin",  fmt=money, optional=False, default="n/a"),
        ]),
        StaticBlock(""),
        SectionBlock(
            label=lambda p: f"Realizzato ({'trader_' + p['trader_id'] if p.get('trader_id') else 'tutti i trader'}):",
            blocks=[
                FieldBlock("  Gross", key="gross_pnl", fmt=money_signed, optional=False, default="n/a"),
                FieldBlock("  Fees",  key="fees",      fmt=money),
                FieldBlock("  Netto", key="net_pnl",   fmt=money_signed, optional=False, default="n/a"),
            ],
        ),
        StaticBlock(""),
        DerivedBlock(lambda p:
            f"Open: {p['open_count']}  |  Waiting: {p['waiting_count']}"
        ),
    ],
)

_DASHBOARD_STATS = TemplateConfig(
    blocks=[
        *_cmd_header("📉", "DASHBOARD — STATS"),
        DerivedBlock(lambda p: p['updated_at']),
        StaticBlock(""),
        BranchBlock(
            condition=lambda p: bool(p.get("periods")),
            then_blocks=[
                TableBlock(
                    rows_key="periods",
                    columns=[
                        ("",       "label",   7, text),
                        ("Trades", "trades",  6, num),
                        ("Win%",   "win_pct", 5, _fmt_win_pct),
                        ("Netto",  "pnl_net", 9, money_signed),
                    ],
                ),
                StaticBlock(""),
                DerivedBlock(lambda p:
                    f"Best:  #{p['best_id']}  {p['best_symbol']}  {money_signed(p['best_pnl'])}"
                    if p.get("best_id") else ""
                ),
                DerivedBlock(lambda p:
                    f"Worst: #{p['worst_id']}  {p['worst_symbol']}  {money_signed(p['worst_pnl'])}"
                    if p.get("worst_id") else ""
                ),
            ],
            else_blocks=[StaticBlock("Nessun trade chiuso.")],
        ),
    ],
)
```

Payload keys dashboard condivise: `account_id`, `trader_id?`, `updated_at`

Payload keys **attivi**: + `snapshot_age`, `snapshot_stale`, `show_trader_tag`,
`rows: list[dict(chain_id, symbol, side, state, sl_price?, has_be,
  entries: list[dict(price, status)],  # status: filled|cancelled|pending
  tps: list[dict(price, status)],
  pnl?, signal_link?, trader_id)]`

Payload keys **chiusi**: + `show_trader_tag`,
`rows: list[dict(chain_id, symbol, side, opened_at, closed_at, duration,
  signal_link?, close_link?, pnl?, trader_id)]`

Payload keys **bloccati**: + `show_trader_tag`,
`rows: list[dict(chain_id, symbol, side, block_type, reason, blocked_at, link?, trader_id)]`
  `block_type`: `REVIEW_REQUIRED` | `EXEC_FAILED`
  `link`: segnale originale (REVIEW_REQUIRED) o tech_log (EXEC_FAILED)

Payload keys **pnl**: + `equity`, `balance`, `margin`, `gross_pnl`, `fees`, `net_pnl`, `open_count`, `waiting_count`

Payload keys **stats**: + `periods`, `best_id`, `best_symbol`, `best_pnl`, `worst_id`, `worst_symbol`, `worst_pnl`

---

## Call pattern — coerente con clean_log e tech_log

Il pattern canonico nel codebase è:

```python
config = TEMPLATE_REGISTRY["trades"]
render_template(config.blocks, payload, transform=config.payload_transform)
```

Per tutti i template commands `payload_transform=None` — il payload è costruito dal formatter
(`_trades_to_payload()`, ecc.), non da una transform function come in clean_log.
La chiamata con `transform=config.payload_transform` è comunque obbligatoria per coerenza:
se in futuro un template aggiunge una transform, viene eseguita automaticamente.

**Naming chiavi registry:** lowercase (`"trades"`, `"close_all_preview"`) vs UPPERCASE di
clean_log/tech_log (`"SIGNAL_ACCEPTED"`). Scelta intenzionale — in commands le chiavi
identificano output di comandi, non notification type names.

---

## Separazione responsabilità

| Layer | Responsabilità |
|---|---|
| `templates/commands.py` | definisce blocchi e TEMPLATE_REGISTRY |
| `formatters/*.py` | converte view → payload dict, chiama render_template |
| `TelegramControlBot` | aggiunge InlineKeyboardMarkup se il comando lo richiede |
| `DashboardManager` | costruisce keyboard dashboard con paginazione condizionale |
| `_blocks.py` | primitivi render — nessuna conoscenza di Telegram |

---

## File toccati

| File | Modifica |
|---|---|
| `formatters/_blocks.py` | + `TableBlock`; `SectionBlock.label` esteso a `str \| Callable` |
| `formatters/templates/commands.py` | nuovo — TEMPLATE_REGISTRY per tutti i comandi |
| `formatters/status.py` | refactor: view → payload → render_template |
| `formatters/trades.py` | refactor: view → payload → render_template |
| `formatters/pnl.py` | refactor: view → payload → render_template |
| `formatters/control.py` | refactor: view → payload → render_template |
| `formatters/stats.py` | nuovo — thin wrapper su render_template |
