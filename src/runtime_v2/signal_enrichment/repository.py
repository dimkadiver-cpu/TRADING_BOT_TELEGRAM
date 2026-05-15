from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from src.parser_v2.contracts.canonical_message import TargetActionGroup
from src.runtime_v2.signal_enrichment.models import (
    EnrichedCanonicalMessage,
    EnrichedSignalPayload,
    EnrichmentLogEntry,
    ManagementPlanConfig,
)


class EnrichedCanonicalMessageRepository:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def save(self, enriched: EnrichedCanonicalMessage) -> EnrichedCanonicalMessage:
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO enriched_canonical_messages (
                    canonical_message_id, raw_message_id, trader_id, account_id,
                    primary_class, enrichment_decision, reason_code,
                    enriched_signal_json, enriched_actions_json, management_plan_json,
                    enrichment_log_json, policy_snapshot_json, policy_version,
                    lifecycle_processed, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    enriched.canonical_message_id,
                    enriched.raw_message_id,
                    enriched.trader_id,
                    enriched.account_id,
                    enriched.primary_class,
                    enriched.enrichment_decision,
                    enriched.reason_code,
                    enriched.enriched_signal.model_dump_json() if enriched.enriched_signal else None,
                    (
                        json.dumps([a.model_dump() for a in enriched.enriched_actions])
                        if enriched.enriched_actions else None
                    ),
                    enriched.management_plan.model_dump_json() if enriched.management_plan else None,
                    json.dumps([e.model_dump() for e in enriched.enrichment_log]),
                    json.dumps(enriched.policy_snapshot),
                    enriched.policy_version,
                    1 if enriched.lifecycle_processed else 0,
                    now,
                ),
            )
            conn.commit()
            if cursor.lastrowid and cursor.rowcount > 0:
                row_id = cursor.lastrowid
            else:
                row = conn.execute(
                    "SELECT enrichment_id FROM enriched_canonical_messages WHERE canonical_message_id = ?",
                    (enriched.canonical_message_id,),
                ).fetchone()
                row_id = row[0]
        finally:
            conn.close()
        return enriched.model_copy(update={"enrichment_id": row_id})

    def get_by_canonical_message_id(self, canonical_message_id: int) -> EnrichedCanonicalMessage | None:
        conn = sqlite3.connect(self._db_path)
        try:
            row = conn.execute(
                """
                SELECT enrichment_id, canonical_message_id, raw_message_id, trader_id,
                       account_id, primary_class, enrichment_decision, reason_code,
                       enriched_signal_json, enriched_actions_json, management_plan_json,
                       enrichment_log_json, policy_snapshot_json, policy_version,
                       lifecycle_processed, created_at
                FROM enriched_canonical_messages WHERE canonical_message_id = ?
                """,
                (canonical_message_id,),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_model(row)
        finally:
            conn.close()

    def _row_to_model(self, row: tuple) -> EnrichedCanonicalMessage:
        (
            enrichment_id, canonical_message_id, raw_message_id, trader_id,
            account_id, primary_class, enrichment_decision, reason_code,
            enriched_signal_json, enriched_actions_json, management_plan_json,
            enrichment_log_json, policy_snapshot_json, policy_version,
            lifecycle_processed, created_at,
        ) = row

        enriched_signal = (
            EnrichedSignalPayload.model_validate_json(enriched_signal_json)
            if enriched_signal_json else None
        )
        enriched_actions = None
        if enriched_actions_json:
            enriched_actions = [
                TargetActionGroup.model_validate(a)
                for a in json.loads(enriched_actions_json)
            ]
        management_plan = (
            ManagementPlanConfig.model_validate_json(management_plan_json)
            if management_plan_json else None
        )
        return EnrichedCanonicalMessage(
            enrichment_id=enrichment_id,
            canonical_message_id=canonical_message_id,
            raw_message_id=raw_message_id,
            trader_id=trader_id,
            account_id=account_id,
            primary_class=primary_class,
            enrichment_decision=enrichment_decision,
            reason_code=reason_code,
            enriched_signal=enriched_signal,
            enriched_actions=enriched_actions,
            management_plan=management_plan,
            enrichment_log=[
                EnrichmentLogEntry.model_validate(e)
                for e in json.loads(enrichment_log_json)
            ],
            policy_snapshot=json.loads(policy_snapshot_json),
            policy_version=policy_version,
            lifecycle_processed=bool(lifecycle_processed),
            created_at=datetime.fromisoformat(created_at) if created_at else None,
        )


__all__ = ["EnrichedCanonicalMessageRepository"]
