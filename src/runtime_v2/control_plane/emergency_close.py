from __future__ import annotations

import json
import secrets
import sqlite3

from src.runtime_v2.control_plane.status_queries import CloseCandidate
from src.runtime_v2.lifecycle.cancel_expander import load_pending_entry_client_order_ids
from src.runtime_v2.lifecycle.models import ExecutionCommand
from src.runtime_v2.lifecycle.repositories import ExecutionCommandRepository

# Tipi reali processati dal gateway (verificati in execution_gateway/order_builder.py).
# NON usare MARKET_CLOSE / CANCEL_ENTRY: non esistono.
_CMD_CLOSE_FULL = "CLOSE_FULL"
_CMD_CANCEL_ENTRY = "CANCEL_PENDING_ENTRY"


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


__all__ = ["EmergencyCloseService"]
