from __future__ import annotations

import unittest

from src.parser.trader_profiles.base import ParserContext
from src.parser.trader_profiles.trader_a.profile import TraderAProfileParser


REAL_CASE_NEW_SIGNAL = (
    "[trader#A]\n\n#ASTERUSDT – Лонг \n\n"
    "Вход (2-фазный):\n"
    "Вход с текущих: 1.0432\n"
    "Усреднение: 0.9823\n\n"
    "Стоп-лосс:\n\n"
    "SL: 0.8461\n\n"
    "Тейки (среднесрок):\n\n"
    "TP1: 1.2417 → Δ ≈ +19.0%, RR ≈ 1:1.0 \n"
    "TP2: 1.3478 → Δ ≈ +29.2%, RR ≈ 1:1.6\n"
    "TP3: 1.5870 → Δ ≈ +52.1%, RR ≈ 1:2.8\n"
    "TP4: 1.8730 → Δ ≈ +79.5%, RR ≈ 1:4.2\n"
    "TP5: 2.1370 → Δ ≈ +104.9%, RR ≈ 1:5.5\n\n"
    "Риск на сделку 0.5-1%"
)

REAL_CASE_STOP_TO_ENTRY = "Можно переставить стоп на точку входа. С учетом усреднения у меня точка входа 1.1340"
REAL_CASE_STOP_HIT = "[trader#A]\n\nК сожалению стоп. Словили -1,5%"
REAL_CASE_EXIT_BE = "Закрылось в БУ"
REAL_CASE_LIMIT_ENTRY = "ETHUSDT Шорт\nВход лимитным ордером: 1.844\nSL: 1.920\nTP1: 1.760"
REAL_CASE_ENTRY_A = "BTCUSDT Лонг\nВход (A): 87600\nSL: 86100\nTP1: 89200"
REAL_CASE_TWO_STEP_AB = (
    "XRPUSDT Лонг\n"
    "Вход (2-фазный):\n"
    "— A (с текущих): 29,45\n"
    "— B (лимит/усреднение): 29,10\n"
    "SL: 28,40\n"
    "TP1: 30,80"
)
REAL_CASE_TWO_STEP_B_MISSING = (
    "DOGEUSDT Лонг\n"
    "Вход (2-фазный):\n"
    "— A (с текущих): 0.11044\n"
    "— B (лимит): -\n"
    "SL: 0.10400\n"
    "TP1: 0.11800"
)
REAL_CASE_BARE_HASHTAG_SYMBOL = " #LINK 🐻 Шорт (вход лимиткой)\nВход лимиткой: 9,05\nSL: 9,25\nTP1: 8,94"
REAL_CASE_SINGLE_TP = "ETHUSDT Шорт\nВход (A): 2906\nSL: 3244\nTP: 2228"
REAL_CASE_SINGLE_RISK = "BTCUSDT LONG\nВход с текущих: 1.2345\nSL: 1.2000\nTP1: 1.2600\nРиск на сделку 1%"


def _risk_signal(risk_line: str) -> str:
    return "BTCUSDT LONG\nВход с текущих: 1.2345\nSL: 1.2000\nTP1: 1.2600\n" + risk_line


def _context(*, text: str, message_id: int, reply_to: int | None = None) -> ParserContext:
    return ParserContext(
        trader_code="trader_a",
        message_id=message_id,
        reply_to_message_id=reply_to,
        channel_id="-1003171748254",
        raw_text=text,
        extracted_links=[],
        hashtags=[],
    )


_SKIP_ENVELOPE = unittest.skip(
    "parse_event_envelope not yet implemented — Phase 4 pending. "
    "These tests define the future TraderEventEnvelopeV1 API for trader_a."
)


class TraderAProfilePhase4CommonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TraderAProfileParser()

    @_SKIP_ENVELOPE
    def test_parse_event_envelope_real_new_signal(self) -> None:
        envelope = self.parser.parse_event_envelope(
            REAL_CASE_NEW_SIGNAL,
            _context(text=REAL_CASE_NEW_SIGNAL, message_id=200),
        )

        self.assertEqual(envelope.message_type_hint, "NEW_SIGNAL")
        self.assertIn("NEW_SETUP", envelope.intents_detected)
        self.assertEqual(envelope.instrument.symbol, "ASTERUSDT")
        self.assertEqual(envelope.instrument.side, "LONG")
        self.assertEqual(envelope.signal_payload_raw.entry_structure, "TWO_STEP")
        self.assertEqual(len(envelope.signal_payload_raw.entries), 2)
        self.assertEqual(envelope.signal_payload_raw.stop_loss.price, 0.8461)
        self.assertEqual(len(envelope.signal_payload_raw.take_profits), 5)
        self.assertIsNone(envelope.signal_payload_raw.risk_hint.value)
        self.assertEqual(envelope.signal_payload_raw.risk_hint.min_value, 0.5)
        self.assertEqual(envelope.signal_payload_raw.risk_hint.max_value, 1.0)
        self.assertEqual(envelope.signal_payload_raw.risk_hint.unit, "PERCENT")

    @_SKIP_ENVELOPE
    def test_parse_event_envelope_real_stop_to_entry_update(self) -> None:
        envelope = self.parser.parse_event_envelope(
            REAL_CASE_STOP_TO_ENTRY,
            _context(text=REAL_CASE_STOP_TO_ENTRY, message_id=262, reply_to=229),
        )

        self.assertEqual(envelope.message_type_hint, "UPDATE")
        self.assertIn("MOVE_STOP_TO_BE", envelope.intents_detected)
        self.assertEqual(envelope.update_payload_raw.stop_update.mode, "TO_ENTRY")
        self.assertIn(229, [target.value for target in envelope.targets_raw if target.kind == "REPLY"])

    @_SKIP_ENVELOPE
    def test_parse_event_envelope_real_stop_hit_report(self) -> None:
        envelope = self.parser.parse_event_envelope(
            REAL_CASE_STOP_HIT,
            _context(text=REAL_CASE_STOP_HIT, message_id=263, reply_to=202),
        )

        self.assertEqual(envelope.message_type_hint, "REPORT")
        self.assertIn("SL_HIT", envelope.intents_detected)
        self.assertEqual([event.event_type for event in envelope.report_payload_raw.events], ["SL_HIT"])
        self.assertEqual(len(envelope.report_payload_raw.reported_results), 1)
        self.assertEqual(envelope.report_payload_raw.reported_results[0].unit, "PERCENT")
        self.assertEqual(envelope.report_payload_raw.reported_results[0].value, -1.5)

    def test_parse_canonical_uses_common_envelope_path(self) -> None:
        message = self.parser.parse_canonical(
            REAL_CASE_NEW_SIGNAL,
            _context(text=REAL_CASE_NEW_SIGNAL, message_id=200),
        )

        self.assertEqual(message.primary_class, "SIGNAL")
        self.assertEqual(message.parse_status, "PARSED")
        self.assertEqual(message.intents, ["NEW_SETUP"])
        self.assertIsNotNone(message.signal)
        assert message.signal is not None
        self.assertEqual(message.signal.symbol, "ASTERUSDT")
        self.assertEqual(message.signal.side, "LONG")
        self.assertEqual(message.signal.entry_structure, "TWO_STEP")
        self.assertEqual(len(message.signal.entries), 2)

    @_SKIP_ENVELOPE
    def test_parse_event_envelope_prefers_exit_be_over_move_stop_to_be(self) -> None:
        envelope = self.parser.parse_event_envelope(
            REAL_CASE_EXIT_BE,
            _context(text=REAL_CASE_EXIT_BE, message_id=264, reply_to=805),
        )

        self.assertEqual(envelope.message_type_hint, "REPORT")
        self.assertEqual(envelope.intents_detected, ["EXIT_BE"])
        self.assertEqual(envelope.primary_intent_hint, "EXIT_BE")

    @_SKIP_ENVELOPE
    def test_parse_event_envelope_limit_entry_variant(self) -> None:
        envelope = self.parser.parse_event_envelope(
            REAL_CASE_LIMIT_ENTRY,
            _context(text=REAL_CASE_LIMIT_ENTRY, message_id=265),
        )

        self.assertEqual(envelope.message_type_hint, "NEW_SIGNAL")
        self.assertEqual(len(envelope.signal_payload_raw.entries), 1)
        self.assertEqual(envelope.signal_payload_raw.entries[0].price, 1.844)
        self.assertEqual(envelope.signal_payload_raw.entries[0].entry_type, "LIMIT")

    @_SKIP_ENVELOPE
    def test_parse_event_envelope_parenthesized_entry_a_variant(self) -> None:
        envelope = self.parser.parse_event_envelope(
            REAL_CASE_ENTRY_A,
            _context(text=REAL_CASE_ENTRY_A, message_id=266),
        )

        self.assertEqual(envelope.message_type_hint, "NEW_SIGNAL")
        self.assertEqual(len(envelope.signal_payload_raw.entries), 1)
        self.assertEqual(envelope.signal_payload_raw.entries[0].price, 87600.0)
        self.assertEqual(envelope.signal_payload_raw.entries[0].entry_type, "LIMIT")

    @_SKIP_ENVELOPE
    def test_parse_event_envelope_two_step_ab_variant(self) -> None:
        envelope = self.parser.parse_event_envelope(
            REAL_CASE_TWO_STEP_AB,
            _context(text=REAL_CASE_TWO_STEP_AB, message_id=267),
        )

        self.assertEqual(envelope.message_type_hint, "NEW_SIGNAL")
        self.assertEqual(envelope.signal_payload_raw.entry_structure, "TWO_STEP")
        self.assertEqual(len(envelope.signal_payload_raw.entries), 2)
        self.assertEqual(envelope.signal_payload_raw.entries[0].price, 29.45)
        self.assertEqual(envelope.signal_payload_raw.entries[0].entry_type, "MARKET")
        self.assertEqual(envelope.signal_payload_raw.entries[1].price, 29.10)
        self.assertEqual(envelope.signal_payload_raw.entries[1].entry_type, "LIMIT")

    @_SKIP_ENVELOPE
    def test_parse_event_envelope_two_step_ab_with_missing_b_keeps_primary(self) -> None:
        envelope = self.parser.parse_event_envelope(
            REAL_CASE_TWO_STEP_B_MISSING,
            _context(text=REAL_CASE_TWO_STEP_B_MISSING, message_id=268),
        )

        self.assertEqual(envelope.message_type_hint, "NEW_SIGNAL")
        self.assertEqual(envelope.signal_payload_raw.entry_structure, "ONE_SHOT")
        self.assertEqual(len(envelope.signal_payload_raw.entries), 1)
        self.assertEqual(envelope.signal_payload_raw.entries[0].price, 0.11044)
        self.assertEqual(envelope.signal_payload_raw.entries[0].entry_type, "MARKET")

    @_SKIP_ENVELOPE
    def test_parse_event_envelope_bare_hashtag_symbol_gets_usdt_suffix(self) -> None:
        envelope = self.parser.parse_event_envelope(
            REAL_CASE_BARE_HASHTAG_SYMBOL,
            _context(text=REAL_CASE_BARE_HASHTAG_SYMBOL, message_id=269),
        )

        self.assertEqual(envelope.message_type_hint, "NEW_SIGNAL")
        self.assertEqual(envelope.instrument.symbol, "LINKUSDT")
        self.assertEqual(envelope.instrument.side, "SHORT")

    @_SKIP_ENVELOPE
    def test_parse_event_envelope_plain_tp_line_is_extracted(self) -> None:
        envelope = self.parser.parse_event_envelope(
            REAL_CASE_SINGLE_TP,
            _context(text=REAL_CASE_SINGLE_TP, message_id=270),
        )

        self.assertEqual(envelope.message_type_hint, "NEW_SIGNAL")
        self.assertEqual(len(envelope.signal_payload_raw.take_profits), 1)
        self.assertEqual(envelope.signal_payload_raw.take_profits[0].sequence, 1)
        self.assertEqual(envelope.signal_payload_raw.take_profits[0].label, "TP1")
        self.assertEqual(envelope.signal_payload_raw.take_profits[0].price, 2228.0)

    @_SKIP_ENVELOPE
    def test_parse_event_envelope_single_risk_value_is_structured(self) -> None:
        envelope = self.parser.parse_event_envelope(
            REAL_CASE_SINGLE_RISK,
            _context(text=REAL_CASE_SINGLE_RISK, message_id=271),
        )

        self.assertEqual(envelope.message_type_hint, "NEW_SIGNAL")
        self.assertIsNotNone(envelope.signal_payload_raw.risk_hint)
        self.assertEqual(envelope.signal_payload_raw.risk_hint.value, 1.0)
        self.assertIsNone(envelope.signal_payload_raw.risk_hint.min_value)
        self.assertIsNone(envelope.signal_payload_raw.risk_hint.max_value)
        self.assertEqual(envelope.signal_payload_raw.risk_hint.unit, "PERCENT")

    @_SKIP_ENVELOPE
    def test_parse_event_envelope_risk_variants_are_structured(self) -> None:
        cases = [
            ("Вход не более 1–2% от депозита", None, 1.0, 2.0),
            ("Риск: не более 0.3–0.5% от депозита", None, 0.3, 0.5),
            ("Вход не более 0.3–0.5% от депозита", None, 0.3, 0.5),
            ("Вход на 1% риска", 1.0, None, None),
            ("1-2% от депозита", None, 1.0, 2.0),
            ("Вход не более 0.5% от депозита", 0.5, None, None),
            ("Заходим не более 1% от депозита", 1.0, None, None),
            ("Вход 1%", 1.0, None, None),
            ("Риск 1% на сделку", 1.0, None, None),
            ("Риск 1% от депозита", 1.0, None, None),
            ("тут риск, зайдем на 0.5% от депозита", 0.5, None, None),
        ]

        for index, (risk_line, expected_value, expected_min, expected_max) in enumerate(cases, start=1):
            text = _risk_signal(risk_line)
            envelope = self.parser.parse_event_envelope(text, _context(text=text, message_id=280 + index))

            with self.subTest(risk_line=risk_line):
                self.assertEqual(envelope.message_type_hint, "NEW_SIGNAL")
                self.assertIsNotNone(envelope.signal_payload_raw.risk_hint)
                self.assertEqual(envelope.signal_payload_raw.risk_hint.value, expected_value)
                self.assertEqual(envelope.signal_payload_raw.risk_hint.min_value, expected_min)
                self.assertEqual(envelope.signal_payload_raw.risk_hint.max_value, expected_max)
                self.assertEqual(envelope.signal_payload_raw.risk_hint.unit, "PERCENT")

    def test_parse_canonical_preserves_risk_range(self) -> None:
        message = self.parser.parse_canonical(
            REAL_CASE_NEW_SIGNAL,
            _context(text=REAL_CASE_NEW_SIGNAL, message_id=272),
        )

        assert message.signal is not None
        assert message.signal.risk_hint is not None
        self.assertIsNone(message.signal.risk_hint.value)
        self.assertEqual(message.signal.risk_hint.min_value, 0.5)
        self.assertEqual(message.signal.risk_hint.max_value, 1.0)
        self.assertEqual(message.signal.risk_hint.unit, "PERCENT")


if __name__ == "__main__":
    unittest.main()
