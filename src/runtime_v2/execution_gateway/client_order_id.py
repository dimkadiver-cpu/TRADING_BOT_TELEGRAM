# src/runtime_v2/execution_gateway/client_order_id.py
from __future__ import annotations

from dataclasses import dataclass

_PREFIX = "tsb"
_VALID_ROLES = frozenset({"entry", "sl", "tp", "exit_partial", "exit_full", "sync"})


@dataclass(frozen=True)
class ClientOrderId:
    trade_chain_id: int
    command_id: int
    role: str
    sequence: int

    def __str__(self) -> str:
        return f"{_PREFIX}:{self.trade_chain_id}:{self.command_id}:{self.role}:{self.sequence}"


def build(trade_chain_id: int, command_id: int, role: str, sequence: int) -> str:
    if role not in _VALID_ROLES:
        raise ValueError(f"Invalid role '{role}'. Must be one of {_VALID_ROLES}")
    return str(ClientOrderId(trade_chain_id, command_id, role, sequence))


def parse(client_order_id: str) -> ClientOrderId:
    parts = client_order_id.split(":")
    if len(parts) != 5 or parts[0] != _PREFIX:
        raise ValueError(f"Invalid client_order_id format: '{client_order_id}'")
    try:
        return ClientOrderId(
            trade_chain_id=int(parts[1]),
            command_id=int(parts[2]),
            role=parts[3],
            sequence=int(parts[4]),
        )
    except (ValueError, IndexError) as e:
        raise ValueError(f"Cannot parse client_order_id '{client_order_id}': {e}") from e


__all__ = ["ClientOrderId", "build", "parse"]
