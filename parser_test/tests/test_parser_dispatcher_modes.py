from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from src.parser.dispatcher import ParserDispatcher
from src.parser.llm_adapter import LLMAdapter
from src.parser.pipeline import MinimalParserPipeline, ParserInput


class _FakeResponse:
    def __init__(self, *, status_code: int, body: dict[str, object], text: str | None = None) -> None:
        self.status_code = status_code
        self._body = body
        self.text = text or json.dumps(body)

    def json(self) -> dict[str, object]:
        return self._body


def _semantic_payload(*, message_type: str = "NEW_SIGNAL", notes: list[str] | None = None) -> dict[str, object]:
    return {
        "message_type": message_type,
        "message_subtype": None,
        "symbol": "BTCUSDT" if message_type == "NEW_SIGNAL" else None,
        "direction": "LONG" if message_type == "NEW_SIGNAL" else None,
        "entries": [{"label": "E1", "price": 90000, "kind": "ENTRY", "raw": "90000"}] if message_type == "NEW_SIGNAL" else [],
        "entry_main": 90000 if message_type == "NEW_SIGNAL" else None,
        "entry_mode": "SINGLE" if message_type == "NEW_SIGNAL" else None,
        "average_entry": 90000 if message_type == "NEW_SIGNAL" else None,
        "stop_loss_price": 89500 if message_type == "NEW_SIGNAL" else None,
        "take_profit_prices": [91000] if message_type == "NEW_SIGNAL" else [],
        "actions": [],
        "target_refs": [],
        "reported_results": [],
        "notes": notes or ["llm_valid"],
        "raw_entities": {"hashtags": [], "links": [], "time_hint": None},
        "confidence": 0.87,
        "validation_warnings": [],
    }


class ParserDispatcherModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_patch = patch.dict(
            os.environ,
            {
                "LLM_PROVIDER": "openai",
                "LLM_MODEL": "gpt-4.1-mini",
                "LLM_TIMEOUT_MS": "12000",
                "OPENAI_API_KEY": "test-openai-key",
                "GEMINI_API_KEY": "test-gemini-key",
            },
            clear=False,
        )
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()

    def _build_pipeline(
        self,
        *,
        mode: str,
        llm_enabled: bool,
        request_fn=None,
        global_llm_provider: str | None = None,
        global_llm_model: str | None = None,
        trader_llm_provider_overrides: dict[str, str] | None = None,
        trader_llm_model_overrides: dict[str, str] | None = None,
    ) -> MinimalParserPipeline:
        resolved_provider = global_llm_provider or os.getenv("LLM_PROVIDER", "openai")
        resolved_model = global_llm_model or os.getenv("LLM_MODEL")
        return MinimalParserPipeline(
            trader_aliases={"TA": "TA", "TB": "TB"},
            global_parser_mode=mode,
            trader_parser_modes={},
            global_llm_provider=resolved_provider,
            global_llm_model=resolved_model,
            trader_llm_provider_overrides=trader_llm_provider_overrides or {},
            trader_llm_model_overrides=trader_llm_model_overrides or {},
            dispatcher=ParserDispatcher(llm_adapter=LLMAdapter(enabled=llm_enabled, request_fn=request_fn)),
        )

    def test_regex_only_uses_regex(self) -> None:
        pipeline = self._build_pipeline(mode="regex_only", llm_enabled=False)
        result = pipeline.parse(
            ParserInput(
                raw_message_id=1,
                raw_text="BTCUSDT long entry 90000 sl 89500 tp1 91000",
                eligibility_status="ACQUIRED_ELIGIBLE",
                eligibility_reason="eligible",
                resolved_trader_id="TB",
                trader_resolution_method="tag",
                linkage_method=None,
                source_chat_id="-1001",
                source_message_id=11,
            )
        )
        normalized = json.loads(result.parse_result_normalized_json or "{}")
        self.assertEqual(normalized.get("parser_used"), "regex")
        self.assertEqual(normalized.get("parser_mode"), "regex_only")

    def test_llm_only_openai_still_works(self) -> None:
        def fake_request(*args, **kwargs):
            self.assertIn("/responses", args[0])
            return _FakeResponse(status_code=200, body={"output_text": json.dumps(_semantic_payload())})

        pipeline = self._build_pipeline(mode="llm_only", llm_enabled=True, request_fn=fake_request)
        result = pipeline.parse(
            ParserInput(
                raw_message_id=2,
                raw_text="BTCUSDT long entry 90000 sl 89500 tp1 91000",
                eligibility_status="ACQUIRED_ELIGIBLE",
                eligibility_reason="eligible",
                resolved_trader_id="TB",
                trader_resolution_method="tag",
                linkage_method=None,
                source_chat_id="-1001",
                source_message_id=12,
            )
        )
        normalized = json.loads(result.parse_result_normalized_json or "{}")
        self.assertEqual(normalized.get("parser_used"), "llm")
        self.assertEqual(normalized.get("parser_mode"), "llm_only")
        self.assertEqual(normalized.get("message_type"), "NEW_SIGNAL")

    def test_llm_only_gemini_valid_response(self) -> None:
        with patch.dict(os.environ, {"LLM_PROVIDER": "gemini", "LLM_MODEL": "gemini-2.5-flash"}, clear=False):
            def fake_request(*args, **kwargs):
                self.assertIn(":generateContent", args[0])
                self.assertEqual(kwargs.get("params", {}).get("key"), "test-gemini-key")
                return _FakeResponse(
                    status_code=200,
                    body={
                        "candidates": [
                            {
                                "content": {
                                    "parts": [
                                        {"text": json.dumps(_semantic_payload(notes=["gemini_valid"]))},
                                    ]
                                }
                            }
                        ]
                    },
                )

            pipeline = self._build_pipeline(mode="llm_only", llm_enabled=True, request_fn=fake_request)
            result = pipeline.parse(
                ParserInput(
                    raw_message_id=3,
                    raw_text="BTCUSDT long entry 90000 sl 89500 tp1 91000",
                    eligibility_status="ACQUIRED_ELIGIBLE",
                    eligibility_reason="eligible",
                    resolved_trader_id="TB",
                    trader_resolution_method="tag",
                    linkage_method=None,
                    source_chat_id="-1001",
                    source_message_id=13,
                )
            )
            normalized = json.loads(result.parse_result_normalized_json or "{}")
            self.assertEqual(normalized.get("parser_used"), "llm")
            self.assertEqual(normalized.get("message_type"), "NEW_SIGNAL")

    def test_gemini_not_configured_is_controlled(self) -> None:
        with patch.dict(
            os.environ,
            {"LLM_PROVIDER": "gemini", "LLM_MODEL": "gemini-2.5-flash", "GEMINI_API_KEY": ""},
            clear=False,
        ):
            pipeline = self._build_pipeline(mode="llm_only", llm_enabled=True)
            result = pipeline.parse(
                ParserInput(
                    raw_message_id=4,
                    raw_text="move sl to breakeven",
                    eligibility_status="ACQUIRED_ELIGIBLE",
                    eligibility_reason="eligible",
                    resolved_trader_id="TB",
                    trader_resolution_method="tag",
                    linkage_method=None,
                    source_chat_id="-1001",
                    source_message_id=14,
                )
            )
            normalized = json.loads(result.parse_result_normalized_json or "{}")
            self.assertEqual(normalized.get("parser_used"), "regex")
            self.assertIn("llm_unavailable", normalized.get("validation_warnings", []))

    def test_hybrid_auto_keeps_regex_when_good(self) -> None:
        pipeline = self._build_pipeline(mode="hybrid_auto", llm_enabled=True, request_fn=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("should not call")))
        result = pipeline.parse(
            ParserInput(
                raw_message_id=5,
                raw_text="BTCUSDT long entry 90000 sl 89500 tp1 91000",
                eligibility_status="ACQUIRED_ELIGIBLE",
                eligibility_reason="eligible",
                resolved_trader_id="TB",
                trader_resolution_method="tag",
                linkage_method=None,
                source_chat_id="-1001",
                source_message_id=15,
            )
        )
        normalized = json.loads(result.parse_result_normalized_json or "{}")
        self.assertEqual(normalized.get("parser_used"), "regex")
        self.assertEqual(normalized.get("selection_metadata", {}).get("selection_reason"), "hybrid_keep_regex")

    def test_hybrid_auto_fallbacks_to_regex_on_invalid_gemini(self) -> None:
        with patch.dict(os.environ, {"LLM_PROVIDER": "gemini", "LLM_MODEL": "gemini-2.5-flash"}, clear=False):
            def fake_bad_request(*args, **kwargs):
                return _FakeResponse(status_code=200, body={"candidates": [{"content": {"parts": [{"text": "not-json"}]}}]})

            pipeline = self._build_pipeline(mode="hybrid_auto", llm_enabled=True, request_fn=fake_bad_request)
            result = pipeline.parse(
                ParserInput(
                    raw_message_id=6,
                    raw_text="move sl to breakeven",
                    eligibility_status="ACQUIRED_ELIGIBLE",
                    eligibility_reason="eligible",
                    resolved_trader_id="TB",
                    trader_resolution_method="tag",
                    linkage_method=None,
                    source_chat_id="-1001",
                    source_message_id=16,
                )
            )
            normalized = json.loads(result.parse_result_normalized_json or "{}")
            self.assertEqual(normalized.get("parser_used"), "regex")
            self.assertEqual(
                normalized.get("selection_metadata", {}).get("selection_reason"),
                "hybrid_llm_unavailable_fallback_regex",
            )

    def test_trader_specific_provider_model_override(self) -> None:
        seen_urls: list[str] = []

        def fake_request(*args, **kwargs):
            seen_urls.append(args[0])
            return _FakeResponse(
                status_code=200,
                body={
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {"text": json.dumps(_semantic_payload(notes=["override_gemini"]))},
                                ]
                            }
                        }
                    ]
                },
            )

        pipeline = self._build_pipeline(
            mode="llm_only",
            llm_enabled=True,
            request_fn=fake_request,
            global_llm_provider="openai",
            global_llm_model="gpt-4.1-mini",
            trader_llm_provider_overrides={"TA": "gemini"},
            trader_llm_model_overrides={"TA": "gemini-2.5-flash"},
        )

        result = pipeline.parse(
            ParserInput(
                raw_message_id=7,
                raw_text="BTCUSDT long entry 90000 sl 89500 tp1 91000",
                eligibility_status="ACQUIRED_ELIGIBLE",
                eligibility_reason="eligible",
                resolved_trader_id="TA",
                trader_resolution_method="tag",
                linkage_method=None,
                source_chat_id="-1001",
                source_message_id=17,
            )
        )
        normalized = json.loads(result.parse_result_normalized_json or "{}")
        self.assertEqual(normalized.get("parser_used"), "llm")
        self.assertTrue(any(":generateContent" in url for url in seen_urls))

    def test_llm_info_only_reported_results(self) -> None:
        def fake_request(*args, **kwargs):
            payload = _semantic_payload(message_type="INFO_ONLY", notes=["weekly report"])
            payload["message_subtype"] = "RESULT_REPORT"
            payload["reported_results"] = [
                {"symbol": "BTC", "r_multiple": 1.2},
                {"symbol": "DOGE", "r_multiple": -0.4},
            ]
            return _FakeResponse(status_code=200, body={"output_text": json.dumps(payload)})

        pipeline = self._build_pipeline(mode="llm_only", llm_enabled=True, request_fn=fake_request)
        result = pipeline.parse(
            ParserInput(
                raw_message_id=8,
                raw_text="weekly update BTC - 1.2R DOGE - -0.4R",
                eligibility_status="ACQUIRED_ELIGIBLE",
                eligibility_reason="eligible",
                resolved_trader_id="TB",
                trader_resolution_method="tag",
                linkage_method=None,
                source_chat_id="-1001",
                source_message_id=18,
            )
        )
        normalized = json.loads(result.parse_result_normalized_json or "{}")
        self.assertEqual(normalized.get("message_type"), "INFO_ONLY")
        self.assertEqual(len(normalized.get("reported_results", [])), 2)


if __name__ == "__main__":
    unittest.main()
