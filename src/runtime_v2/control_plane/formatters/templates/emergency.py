# src/runtime_v2/control_plane/formatters/templates/emergency.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import (
    BranchBlock, DerivedBlock, ListBlock,
    SeparatorBlock, StaticBlock, TemplateConfig,
)


def _chain_renderer_with_state(c: dict, i: int, p: dict) -> list[str]:
    side_emoji = "📈" if c["side"] == "LONG" else "📉"
    return [f"#{c['chain_id']}  {side_emoji} {c['symbol']}  {c['side']}  {c['state']}"]


def _chain_renderer_compact(c: dict, i: int, p: dict) -> list[str]:
    side_emoji = "📈" if c["side"] == "LONG" else "📉"
    return [f"#{c['chain_id']}  {side_emoji} {c['symbol']}  {c['side']}"]


# ── /close_all ───────────────────────────────────────────────────────────────

_CLOSE_ALL_PREVIEW = TemplateConfig(blocks=[
    DerivedBlock(text_fn=lambda p: f"🚨 CLOSE ALL — {p['scope_label']}"),
    SeparatorBlock(),
    BranchBlock(
        condition=lambda p: p["total"] == 0,
        then_blocks=[StaticBlock("Nessuna posizione aperta da chiudere.")],
        else_blocks=[
            DerivedBlock(text_fn=lambda p: f"Posizioni da chiudere: {p['total']}"),
            SeparatorBlock(),
            ListBlock(key="chains", item_renderer=_chain_renderer_with_state),
            SeparatorBlock(),
            StaticBlock("⚠️ Verranno inviati ordini MARKET di chiusura."),
            SeparatorBlock(),
            StaticBlock("Confermi?"),
        ],
    ),
])

_CLOSE_ALL_RESULT_OK = TemplateConfig(blocks=[
    DerivedBlock(text_fn=lambda p: f"🚨 CLOSE ALL — {p['scope_label']}"),
    SeparatorBlock(),
    ListBlock(key="chains", item_renderer=_chain_renderer_compact),
    SeparatorBlock(),
    DerivedBlock(text_fn=lambda p: f"✅ ESEGUITO — {p['executed_at']}"),
    DerivedBlock(text_fn=lambda p: f"{p['count']} comandi CLOSE_FULL inseriti."),
    StaticBlock("⚡ Monitorare con /trades"),
])

_CLOSE_ALL_RESULT_CANCELLED = TemplateConfig(blocks=[
    DerivedBlock(text_fn=lambda p: f"🚨 CLOSE ALL — {p['scope_label']}"),
    SeparatorBlock(),
    ListBlock(key="chains", item_renderer=_chain_renderer_compact),
    SeparatorBlock(),
    DerivedBlock(text_fn=lambda p: f"❌ ANNULLATO — {p['cancelled_at']}"),
    StaticBlock("Nessuna azione eseguita."),
])


# ── /close ───────────────────────────────────────────────────────────────────

def _close_single_preview_chain(c: dict, i: int, p: dict) -> list[str]:
    side_emoji = "📈" if c["side"] == "LONG" else "📉"
    lines = [f"#{c['chain_id']}  {side_emoji} {c['symbol']}  {c['side']}  {c['state']}"]
    if c.get("entry_price"):
        lines.append(f"    Entry: {c['entry_price']}  |  PnL: {c.get('pnl', 'n/a')}")
    return lines


_CLOSE_SINGLE_PREVIEW = TemplateConfig(blocks=[
    DerivedBlock(text_fn=lambda p: f"🚨 CLOSE — {p['scope_label']}"),
    SeparatorBlock(),
    BranchBlock(
        condition=lambda p: p["total"] == 0,
        then_blocks=[DerivedBlock(text_fn=lambda p: f"{p['symbol']}: nessuna posizione aperta trovata.")],
        else_blocks=[
            BranchBlock(
                condition=lambda p: p["total"] == 1,
                then_blocks=[StaticBlock("Posizione da chiudere:")],
                else_blocks=[DerivedBlock(text_fn=lambda p: f"Trovate {p['total']} posizioni su {p['symbol']}:")],
            ),
            SeparatorBlock(),
            ListBlock(key="chains", item_renderer=_close_single_preview_chain),
            SeparatorBlock(),
            StaticBlock("⚠️ Verrà inviato un ordine MARKET di chiusura."),
            SeparatorBlock(),
            StaticBlock("Confermi?"),
        ],
    ),
])

_CLOSE_SINGLE_RESULT_OK = TemplateConfig(blocks=[
    DerivedBlock(text_fn=lambda p: f"🚨 CLOSE — {p['scope_label']}"),
    SeparatorBlock(),
    ListBlock(key="chains", item_renderer=_chain_renderer_compact),
    SeparatorBlock(),
    DerivedBlock(text_fn=lambda p: f"✅ ESEGUITO — {p['executed_at']}"),
    DerivedBlock(text_fn=lambda p: f"{p['count']} {'comando' if p['count'] == 1 else 'comandi'} CLOSE_FULL inserito."),
    DerivedBlock(text_fn=lambda p: f"⚡ Monitorare con {'  /trade #' + str(p['chains'][0]['chain_id']) if p['count'] == 1 else '/trades'}"),
])

_CLOSE_SINGLE_RESULT_CANCELLED = TemplateConfig(blocks=[
    DerivedBlock(text_fn=lambda p: f"🚨 CLOSE — {p['scope_label']}"),
    SeparatorBlock(),
    ListBlock(key="chains", item_renderer=_chain_renderer_compact),
    SeparatorBlock(),
    DerivedBlock(text_fn=lambda p: f"❌ ANNULLATO — {p['cancelled_at']}"),
])


# ── /cancel_all ──────────────────────────────────────────────────────────────

def _waiting_renderer_with_state(c: dict, i: int, p: dict) -> list[str]:
    side_emoji = "📈" if c["side"] == "LONG" else "📉"
    return [f"#{c['chain_id']}  {side_emoji} {c['symbol']}  {c['side']}  WAITING_ENTRY"]


def _waiting_renderer_compact(c: dict, i: int, p: dict) -> list[str]:
    side_emoji = "📈" if c["side"] == "LONG" else "📉"
    return [f"#{c['chain_id']}  {c['symbol']}  {c['side']}"]


_CANCEL_ALL_PREVIEW = TemplateConfig(blocks=[
    DerivedBlock(text_fn=lambda p: f"🛑 CANCEL ALL — {p['scope_label']}"),
    SeparatorBlock(),
    BranchBlock(
        condition=lambda p: p["total"] == 0,
        then_blocks=[StaticBlock("Nessun ordine WAITING_ENTRY da cancellare.")],
        else_blocks=[
            DerivedBlock(text_fn=lambda p: f"Ordini entry in attesa: {p['total']}"),
            SeparatorBlock(),
            ListBlock(key="chains", item_renderer=_waiting_renderer_with_state),
            SeparatorBlock(),
            DerivedBlock(text_fn=lambda p: f"Posizioni aperte non toccate: {p['open_count']}"),
            SeparatorBlock(),
            StaticBlock("Confermi la cancellazione?"),
        ],
    ),
])

_CANCEL_ALL_RESULT_OK = TemplateConfig(blocks=[
    DerivedBlock(text_fn=lambda p: f"🛑 CANCEL ALL — {p['scope_label']}"),
    SeparatorBlock(),
    ListBlock(key="chains", item_renderer=_waiting_renderer_compact),
    SeparatorBlock(),
    DerivedBlock(text_fn=lambda p: f"✅ ESEGUITO — {p['executed_at']}"),
    DerivedBlock(text_fn=lambda p: f"{p['count']} ordini WAITING_ENTRY cancellati."),
    DerivedBlock(text_fn=lambda p: f"Posizioni aperte non toccate: {p['open_count']}"),
    StaticBlock("/trades per verificare."),
])

_CANCEL_ALL_RESULT_CANCELLED = TemplateConfig(blocks=[
    DerivedBlock(text_fn=lambda p: f"🛑 CANCEL ALL — {p['scope_label']}"),
    SeparatorBlock(),
    ListBlock(key="chains", item_renderer=_waiting_renderer_compact),
    SeparatorBlock(),
    DerivedBlock(text_fn=lambda p: f"❌ ANNULLATO — {p['cancelled_at']}"),
])


EMERGENCY_REGISTRY: dict[str, TemplateConfig] = {
    "close_all_preview": _CLOSE_ALL_PREVIEW,
    "close_all_result_ok": _CLOSE_ALL_RESULT_OK,
    "close_all_result_cancelled": _CLOSE_ALL_RESULT_CANCELLED,
    "close_single_preview": _CLOSE_SINGLE_PREVIEW,
    "close_single_result_ok": _CLOSE_SINGLE_RESULT_OK,
    "close_single_result_cancelled": _CLOSE_SINGLE_RESULT_CANCELLED,
    "cancel_all_preview": _CANCEL_ALL_PREVIEW,
    "cancel_all_result_ok": _CANCEL_ALL_RESULT_OK,
    "cancel_all_result_cancelled": _CANCEL_ALL_RESULT_CANCELLED,
}

__all__ = ["EMERGENCY_REGISTRY"]
