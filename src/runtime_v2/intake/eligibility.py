from __future__ import annotations

from dataclasses import dataclass

from src.runtime_v2.intake.models import RawMessageEnvelope
from src.telegram.eligibility import MessageEligibilityEvaluator
from src.storage.raw_messages import RawMessageStore


@dataclass(slots=True, frozen=True)
class EligibilityOutcome:
    eligible: bool
    review_reason: str | None


class IntakeEligibilityCheck:
    """Wraps MessageEligibilityEvaluator with the runtime_v2 contract.

    Contract: check(envelope) -> EligibilityOutcome(eligible, review_reason)
    """

    def __init__(self, raw_store: RawMessageStore) -> None:
        self._evaluator = MessageEligibilityEvaluator(raw_store)

    def check(self, envelope: RawMessageEnvelope) -> EligibilityOutcome:
        result = self._evaluator.evaluate(
            source_chat_id=envelope.source_chat_id,
            raw_text=envelope.raw_text,
            reply_to_message_id=envelope.reply_to_message_id,
        )
        if result.is_eligible:
            return EligibilityOutcome(eligible=True, review_reason=None)
        return EligibilityOutcome(eligible=False, review_reason=result.reason)
