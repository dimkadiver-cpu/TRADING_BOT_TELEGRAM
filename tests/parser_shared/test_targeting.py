"""Tests for shared targeting module: parser-side target ref extraction."""

from __future__ import annotations

from src.parser.trader_profiles.shared.targeting import (
    TargetRefRaw,
    extract_targets,
    build_reply_ref,
    build_telegram_link_ref,
    build_explicit_id_ref,
    build_symbol_ref,
    build_global_scope_ref,
    KNOWN_GLOBAL_SCOPES,
)


class TestBuildRefs:
    def test_reply_ref(self) -> None:
        ref = build_reply_ref(message_id=555)
        assert ref.kind == "REPLY"
        assert ref.value == 555

    def test_telegram_link_ref(self) -> None:
        url = "https://t.me/c/123/456"
        ref = build_telegram_link_ref(url=url)
        assert ref.kind == "TELEGRAM_LINK"
        assert ref.value == url

    def test_explicit_id_ref(self) -> None:
        ref = build_explicit_id_ref(message_id=789)
        assert ref.kind == "MESSAGE_ID"
        assert ref.value == 789

    def test_symbol_ref(self) -> None:
        ref = build_symbol_ref(symbol="BTCUSDT")
        assert ref.kind == "SYMBOL"
        assert ref.value == "BTCUSDT"

    def test_global_scope_ref(self) -> None:
        ref = build_global_scope_ref(scope="ALL_POSITIONS")
        assert ref.kind == "UNKNOWN"  # global scope uses UNKNOWN kind per spec
        assert ref.value == "ALL_POSITIONS"


class TestExtractTargets:
    def test_reply_adds_reply_ref(self) -> None:
        refs = extract_targets(
            reply_to_message_id=100,
            text="move stop",
            extracted_links=[],
        )
        kinds = [r.kind for r in refs]
        assert "REPLY" in kinds

    def test_telegram_link_adds_link_and_message_id_refs(self) -> None:
        refs = extract_targets(
            reply_to_message_id=None,
            text="close all https://t.me/c/123/456",
            extracted_links=["https://t.me/c/123/456"],
        )
        kinds = [r.kind for r in refs]
        assert "TELEGRAM_LINK" in kinds
        assert "MESSAGE_ID" in kinds

    def test_multiple_links_produce_multiple_refs(self) -> None:
        refs = extract_targets(
            reply_to_message_id=None,
            text="",
            extracted_links=["https://t.me/c/10/101", "https://t.me/c/10/102"],
        )
        link_refs = [r for r in refs if r.kind == "TELEGRAM_LINK"]
        assert len(link_refs) == 2

    def test_no_target_produces_empty_list(self) -> None:
        refs = extract_targets(
            reply_to_message_id=None,
            text="good morning",
            extracted_links=[],
        )
        assert refs == []

    def test_reply_and_link_both_included(self) -> None:
        refs = extract_targets(
            reply_to_message_id=42,
            text="update https://t.me/c/1/2",
            extracted_links=["https://t.me/c/1/2"],
        )
        kinds = {r.kind for r in refs}
        assert "REPLY" in kinds
        assert "TELEGRAM_LINK" in kinds


class TestKnownGlobalScopes:
    def test_all_positions_is_known(self) -> None:
        assert "ALL_POSITIONS" in KNOWN_GLOBAL_SCOPES

    def test_all_longs_is_known(self) -> None:
        assert "ALL_LONGS" in KNOWN_GLOBAL_SCOPES

    def test_all_shorts_is_known(self) -> None:
        assert "ALL_SHORTS" in KNOWN_GLOBAL_SCOPES
