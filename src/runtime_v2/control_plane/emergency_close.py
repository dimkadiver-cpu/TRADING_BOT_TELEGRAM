from __future__ import annotations

import json
import secrets
import sqlite3

from src.runtime_v2.control_plane.scope_resolver import QueryScope
from src.runtime_v2.control_plane.status_queries import CloseCandidate, StatusQueries
from src.runtime_v2.lifecycle.cancel_expander import load_pending_entry_client_order_ids
from src.runtime_v2.lifecycle.models import ExecutionCommand
from src.runtime_v2.lifecycle.repositories import ExecutionCommandRepository

# Tipi reali processati dal gateway (verificati in execution_gateway/order_builder.py).
# NON usare MARKET_CLOSE / CANCEL_ENTRY: non esistono.
_CMD_CLOSE_FULL = "CLOSE_FULL"
_CMD_CANCEL_ENTRY = "CANCEL_PENDING_ENTRY"

# ---------------------------------------------------------------------------
# Safety: rifiuto in global scope non filtrato
# ---------------------------------------------------------------------------

GLOBAL_SCOPE_SAFETY_MSG = (
    "⛔ Comando non disponibile in All accounts senza filtro.\n"
    "Specifica trader o account: es. /close_all trader_a"
)


def _is_unfiltered_global(scope: QueryScope, trader_filter: str | None = None) -> bool:
    """True se scope globale E nessun filtro esplicito applicato."""
    return scope.account_id is None and not trader_filter and not scope.trader_ids


def build_close_all_preview(
    scope: QueryScope,
    ops_db: str,
    trader_filter: str | None = None,
) -> str | None:
    """Costruisce il messaggio di preview per /close_all.

    Ritorna GLOBAL_SCOPE_SAFETY_MSG se il comando sarebbe eseguito in global
    scope senza alcun filtro esplicito. Ritorna None se non ci sono candidati.
    Altrimenti ritorna una stringa con la lista dei trade che verrebbero chiusi.
    """
    if _is_unfiltered_global(scope, trader_filter):
        return GLOBAL_SCOPE_SAFETY_MSG

    q = StatusQueries(ops_db)
    candidates = q.get_open_for_close(scope)
    if not candidates:
        return None

    lines = [f"🔴 /close_all — {len(candidates)} trade da chiudere:"]
    for c in candidates:
        lines.append(f"  #{c.chain_id} {c.symbol} {c.side} [{c.state}]")
    return "\n".join(lines)


def build_cancel_all_preview(
    scope: QueryScope,
    ops_db: str,
    trader_filter: str | None = None,
) -> str | None:
    """Costruisce il messaggio di preview per /cancel_all.

    Ritorna GLOBAL_SCOPE_SAFETY_MSG se il comando sarebbe eseguito in global
    scope senza alcun filtro esplicito. Ritorna None se non ci sono candidati.
    Altrimenti ritorna una stringa con la lista degli ordini che verrebbero cancellati.
    """
    if _is_unfiltered_global(scope, trader_filter):
        return GLOBAL_SCOPE_SAFETY_MSG

    q = StatusQueries(ops_db)
    candidates = q.get_waiting_for_cancel(scope)
    if not candidates:
        return None

    lines = [f"🟡 /cancel_all — {len(candidates)} ordine/i da cancellare:"]
    for c in candidates:
        lines.append(f"  #{c.chain_id} {c.symbol} {c.side} [{c.state}]")
    return "\n".join(lines)


class EmergencyCloseService:
    """Crea comandi di chiusura/cancellazione via il repository esistente.

    Riusa ExecutionCommandRepository.save() (lifecycle/repositories.py): popola
    idempotency_key/created_at/updated_at e usa INSERT OR IGNORE. NON scrive INSERT
    a mano (lo schema reale richiede idempotency_key NOT NULL UNIQUE + updated_at
    NOT NULL e non ha colonna created_by — created_by resta solo nell'audit comando).
    """

    def __init__(self, ops_db_path: str) -> None:
        self._db = ops_db_path
        self._repo = ExecutionCommandRepository(ops_db_path)

    def execute_close(self, candidates: list[CloseCandidate], created_by: str) -> int:
        """Crea un CLOSE_FULL per ogni candidato. Ritorna count creati.

        Usa il symbol RAW del candidato (CloseCandidate.symbol), non quello display.
        """
        count = 0
        for c in candidates:
            cmd = ExecutionCommand(
                trade_chain_id=c.chain_id,
                command_type=_CMD_CLOSE_FULL,
                status="PENDING",
                payload_json=json.dumps({"symbol": c.symbol, "side": c.side}),
                idempotency_key=f"manual_close:{c.chain_id}:{secrets.token_hex(4)}",
            )
            self._repo.save(cmd)
            count += 1
        return count

    def execute_cancel(self, candidates: list[CloseCandidate], created_by: str) -> int:
        """Crea i CANCEL_PENDING_ENTRY espandendoli per gamba entry reale.

        Ritorna il numero di comandi effettivamente creati (≥0). Una chain senza
        entry pendenti con client_order_id reale non produce comandi.
        """
        count = 0
        conn = sqlite3.connect(self._db)
        try:
            for c in candidates:
                coids = load_pending_entry_client_order_ids(conn, c.chain_id)
                for coid in coids:
                    cmd = ExecutionCommand(
                        trade_chain_id=c.chain_id,
                        command_type=_CMD_CANCEL_ENTRY,
                        status="PENDING",
                        payload_json=json.dumps(
                            {"symbol": c.symbol, "entry_client_order_id": coid}
                        ),
                        idempotency_key=f"cancel_entry:{c.chain_id}:{coid}:{secrets.token_hex(4)}",
                    )
                    self._repo.save(cmd)
                    count += 1
        finally:
            conn.close()
        return count


__all__ = [
    "EmergencyCloseService",
    "GLOBAL_SCOPE_SAFETY_MSG",
    "build_close_all_preview",
    "build_cancel_all_preview",
]
