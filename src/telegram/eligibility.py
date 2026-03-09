"""Pre-parser message eligibility checks."""

from __future__ import annotations

from dataclasses import dataclass
import re

from src.storage.raw_messages import RawMessageStore

_TELEGRAM_LINK_RE = re.compile(r"(?:https?://)?t\.me/(?:c/\d+|[A-Za-z0-9_]+)/(\d+)", re.IGNORECASE)
_EXPLICIT_REF_RE = re.compile(r"(?:msg|message|ref|id)\s*#?:?\s*(\d{2,})", re.IGNORECASE)
_HASHTAG_REF_RE = re.compile(r"#(\d{3,})")


@dataclass(slots=True)
class EligibilityResult:
    is_eligible: bool
    status: str
    reason: str
    strong_link_method: str | None = None
    referenced_message_id: int | None = None


class MessageEligibilityEvaluator:
    def __init__(self, raw_store: RawMessageStore) -> None:
        self._raw_store = raw_store

    def evaluate(
        self,
        source_chat_id: str,
        raw_text: str | None,
        reply_to_message_id: int | None,
    ) -> EligibilityResult:
        short_update = self._looks_like_short_update(raw_text)
        strong_link_method, ref_id = self._strong_link(source_chat_id, raw_text, reply_to_message_id)
        if short_update and strong_link_method is None:
            return EligibilityResult(
                is_eligible=False,
                status="ACQUIRED_REVIEW_ONLY",
                reason="short_update_without_strong_link",
            )
        return EligibilityResult(
            is_eligible=True,
            status="ACQUIRED_ELIGIBLE",
            reason="eligible",
            strong_link_method=strong_link_method,
            referenced_message_id=ref_id,
        )

    def _strong_link(
        self,
        source_chat_id: str,
        raw_text: str | None,
        reply_to_message_id: int | None,
    ) -> tuple[str | None, int | None]:
        if reply_to_message_id is not None:
            parent = self._raw_store.get_by_source_and_message_id(source_chat_id, reply_to_message_id)
            if parent is not None:
                return "direct_reply", reply_to_message_id

        text = (raw_text or "").strip()
        link_match = _TELEGRAM_LINK_RE.search(text)
        if link_match:
            return "telegram_link", int(link_match.group(1))

        explicit = _EXPLICIT_REF_RE.search(text) or _HASHTAG_REF_RE.search(text)
        if explicit:
            return "explicit_reference", int(explicit.group(1))
        return None, None

    @staticmethod
    def _looks_like_short_update(raw_text: str | None) -> bool:
        if not raw_text:
            return False
        text = raw_text.strip().lower()
        if not text:
            return False
        # Guardrail: full setup-like messages are not "short updates"
        has_setup_shape = ("entry" in text) and (("sl" in text) or ("stop" in text)) and (
            ("tp" in text) or ("target" in text)
        )
        if has_setup_shape:
            return False
        short_shape = len(text) <= 120 and text.count("\n") <= 1
        if not short_shape:
            return False
        update_terms = ("sl", "tp", "close", "be", "breakeven", "move stop", "secured", "target")
        return any(term in text for term in update_terms)
