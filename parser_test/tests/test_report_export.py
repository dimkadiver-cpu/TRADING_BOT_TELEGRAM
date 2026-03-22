from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import types
from pathlib import Path
import unittest
from unittest.mock import patch

from parser_test.reporting.flatteners import build_report_row
from parser_test.reporting.report_export import export_reports_csv_v2


class ReportingCsvExportTests(unittest.TestCase):
    def _make_db(self) -> Path:
        tmp = tempfile.NamedTemporaryFile(prefix='tsb_reporting_', suffix='.sqlite3', delete=False)
        tmp.close()
        db_path = Path(tmp.name)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                'CREATE TABLE raw_messages (raw_message_id INTEGER PRIMARY KEY, raw_text TEXT, reply_to_message_id INTEGER, message_ts TEXT, source_chat_id TEXT, telegram_message_id TEXT)'
            )
            conn.execute(
                'CREATE TABLE parse_results (raw_message_id INTEGER, resolved_trader_id TEXT, message_type TEXT, parse_status TEXT, warning_text TEXT, parse_result_normalized_json TEXT)'
            )
            conn.executemany(
                'INSERT INTO raw_messages(raw_message_id, raw_text, reply_to_message_id, message_ts, source_chat_id, telegram_message_id) VALUES (?, ?, ?, ?, ?, ?)',
                [
                    (1, 'BTCUSDT long entry 100 sl 90 tp1 110', None, '2026-01-01T10:00:00Z', '-100', '1'),
                    (2, 'TAOUSDT tp1 hit https://t.me/c/1/17', 1, '2026-01-01T10:01:00Z', '-100', '2'),
                ],
            )
            conn.executemany(
                'INSERT INTO parse_results(raw_message_id, resolved_trader_id, message_type, parse_status, warning_text, parse_result_normalized_json) VALUES (?, ?, ?, ?, ?, ?)',
                [
                    (
                        1,
                        'trader_a',
                        'NEW_SIGNAL',
                        'PARSED',
                        '',
                        json.dumps(
                            {
                                'event_type': 'NEW_SIGNAL',
                                'message_class': 'NEW_SIGNAL',
                                'message_type': 'NEW_SIGNAL',
                                'primary_intent': 'NS_CREATE_SIGNAL',
                                'intents': ['NS_CREATE_SIGNAL'],
                                'symbol': 'BTCUSDT',
                                'direction': 'LONG',
                                'market_type': 'LINEAR',
                                'status': 'PARSED',
                                'confidence': 0.91,
                                'parser_used': 'regex',
                                'parser_mode': 'regex_only',
                                'entries': [{'role': 'PRIMARY', 'order_type': 'LIMIT', 'price': 100.0}],
                                'entry_plan_type': 'SINGLE_LIMIT',
                                'entry_structure': 'SINGLE',
                                'has_averaging_plan': False,
                                'stop_loss_price': 90.0,
                                'take_profit_prices': [110.0],
                                'target_refs': [],
                                'root_ref': 1,
                                'target_scope': {'kind': 'signal', 'scope': 'single', 'root_ref': 1},
                                'linking': {'strategy': 'reply_or_link', 'target_refs_count': 0, 'root_ref': 1, 'extracted_target_refs': []},
                                'actions_structured': [
                                    {'action_type': 'CREATE_SIGNAL', 'symbol': 'BTCUSDT', 'entry_plan_type': 'SINGLE_LIMIT'}
                                ],
                                'validation_warnings': [],
                                'reported_results': [],
                                'diagnostics': {'parser_mode': 'regex_only'},
                            },
                            ensure_ascii=False,
                        ),
                    ),
                    (
                        2,
                        'trader_a',
                        'UPDATE',
                        'PARSED',
                        '',
                        json.dumps(
                            {
                                'event_type': 'UPDATE',
                                'message_class': 'UPDATE',
                                'message_type': 'UPDATE',
                                'primary_intent': 'U_TP_HIT',
                                'intents': ['U_TP_HIT', 'U_REPORT_FINAL_RESULT'],
                                'symbol': 'TAOUSDT',
                                'status': 'PARSED',
                                'confidence': 0.83,
                                'parser_used': 'regex',
                                'parser_mode': 'regex_only',
                                'target_refs': [17],
                                'root_ref': 17,
                                'take_profit_prices': [1.245, 1.31],
                                'results_v2': [
                                    {'symbol': 'TAOUSDT', 'value': 15.0, 'unit': 'PERCENT', 'leverage_hint': 5.0, 'result_type': 'PERCENT'}
                                ],
                                'reported_results': [{'symbol': 'TAOUSDT', 'r_multiple': 15.0, 'unit': 'PERCENT'}],
                                'entities': {
                                    'hit_target': 'TP1',
                                    'result_mode': 'PERCENT',
                                    'reported_profit_percent': 15.0,
                                    'reported_leverage_hint': 5.0,
                                },
                                'target_scope': {
                                    'kind': 'signal',
                                    'scope': 'single',
                                    'root_ref': 17,
                                    'target_refs': [17, 18],
                                    'extracted_target_refs': [17, 18],
                                },
                                'linking': {
                                    'strategy': 'reply_or_link',
                                    'target_ref_count': 2,
                                    'root_ref': 17,
                                    'extracted_target_refs': [17, 18],
                                },
                                'actions_structured': [
                                    {'action_type': 'MARK_TP_HIT', 'intent': 'U_TP_HIT', 'hit_target': 'TP1', 'target_refs': [17], 'target_refs_count': 1, 'result_mode': 'PERCENT'},
                                    {'action_type': 'ATTACH_RESULT', 'intent': 'U_REPORT_FINAL_RESULT', 'reported_results': [{'symbol': 'TAOUSDT', 'value': 15.0, 'unit': 'PERCENT'}], 'result_mode': 'PERCENT'},
                                ],
                                'validation_warnings': [],
                                'diagnostics': {'parser_mode': 'regex_only'},
                            },
                            ensure_ascii=False,
                        ),
                    ),
                ],
            )
            conn.commit()
        return db_path

    def test_build_report_row_flattens_tp_hit_v2_without_legacy_columns(self) -> None:
        row = build_report_row(
            raw_message_id=2,
            parse_status='PARSED',
            reply_to_message_id=1,
            raw_text='TAOUSDT tp1 hit https://t.me/c/1/17',
            warning_text='',
            normalized={
                'event_type': 'UPDATE',
                'message_class': 'UPDATE',
                'message_type': 'UPDATE',
                'primary_intent': 'U_TP_HIT',
                'intents': ['U_TP_HIT', 'U_REPORT_FINAL_RESULT'],
                'symbol': 'TAOUSDT',
                'status': 'PARSED',
                'confidence': 0.83,
                'parser_used': 'regex',
                'parser_mode': 'regex_only',
                'target_refs': [17],
                'root_ref': 17,
                'take_profit_prices': [1.245, 1.31],
                'results_v2': [
                    {'symbol': 'TAOUSDT', 'value': 15.0, 'unit': 'PERCENT', 'leverage_hint': 5.0, 'result_type': 'PERCENT'}
                ],
                'reported_results': [{'symbol': 'TAOUSDT', 'r_multiple': 15.0, 'unit': 'PERCENT'}],
                'entities': {
                    'hit_target': 'TP1',
                    'result_mode': 'PERCENT',
                    'reported_profit_percent': 15.0,
                    'reported_leverage_hint': 5.0,
                },
                'target_scope': {
                    'kind': 'signal',
                    'scope': 'single',
                    'root_ref': 17,
                    'target_refs': [17, 18],
                    'extracted_target_refs': [17, 18],
                },
                'linking': {
                    'strategy': 'reply_or_link',
                    'target_ref_count': 2,
                    'root_ref': 17,
                    'extracted_target_refs': [17, 18],
                },
                'actions_structured': [
                    {'action_type': 'MARK_TP_HIT', 'intent': 'U_TP_HIT', 'hit_target': 'TP1', 'target_refs': [17], 'target_refs_count': 1, 'result_mode': 'PERCENT'},
                    {'action_type': 'ATTACH_RESULT', 'intent': 'U_REPORT_FINAL_RESULT', 'reported_results': [{'symbol': 'TAOUSDT', 'value': 15.0, 'unit': 'PERCENT'}], 'result_mode': 'PERCENT'},
                ],
                'validation_warnings': [],
                'diagnostics': {'parser_mode': 'regex_only'},
            },
            scope='UPDATE',
        )
        self.assertEqual(row['primary_intent'], 'U_TP_HIT')
        self.assertEqual(row['action_types'], 'MARK_TP_HIT | ATTACH_RESULT')
        self.assertEqual(row['symbol'], 'TAOUSDT')
        self.assertEqual(row['signal_id'], '17')
        self.assertEqual(row['target_refs'], '17')
        self.assertEqual(row['target_refs_count'], '1')
        self.assertEqual(row['linking_strategy'], 'reply_or_link')
        self.assertEqual(row['hit_target'], 'TP1')
        self.assertEqual(row['tp_prices'], '1.245 | 1.31')
        self.assertEqual(row['reported_profit_percent'], '15')
        self.assertEqual(row['reported_leverage_hint'], '5')
        self.assertNotIn('legacy_actions', row)
        self.assertNotIn('normalized_json_debug', row)

    def test_export_reports_csv_standard_excludes_legacy_and_json_debug(self) -> None:
        db_path = self._make_db()
        reports_dir = Path.cwd() / 'parser_test' / 'reports'
        try:
            with patch('parser_test.reporting.report_export.load_config') as load_config:
                load_config.return_value = types.SimpleNamespace(traders={'trader_a': {}, 'trader_b': {}})
                updated = export_reports_csv_v2(db_path=db_path, reports_dir=reports_dir, trader='all')
            self.assertTrue(updated)
            update_csv = reports_dir / 'trader_a_message_types_csv' / 'trader_a_update.csv'
            with update_csv.open('r', encoding='utf-8-sig', newline='') as handle:
                reader = csv.DictReader(handle)
                header = reader.fieldnames or []
                rows = list(reader)
            self.assertIn('action_types', header)
            self.assertIn('actions_structured_summary', header)
            self.assertNotIn('actions', header)
            self.assertNotIn('legacy_actions', header)
            self.assertNotIn('normalized_json_debug', header)
            self.assertTrue(rows)
            self.assertEqual(rows[0]['action_types'], 'MARK_TP_HIT | ATTACH_RESULT')
            self.assertEqual(rows[0]['target_refs_count'], '1')
        finally:
            for suffix in ('', '-wal', '-shm'):
                try:
                    db_path.with_suffix(db_path.suffix + suffix).unlink()
                except (FileNotFoundError, PermissionError):
                    pass

    def test_export_reports_csv_can_include_debug_columns_on_flag(self) -> None:
        db_path = self._make_db()
        reports_dir = Path.cwd() / 'parser_test' / 'reports'
        try:
            with patch('parser_test.reporting.report_export.load_config') as load_config:
                load_config.return_value = types.SimpleNamespace(traders={'trader_a': {}})
                export_reports_csv_v2(
                    db_path=db_path,
                    reports_dir=reports_dir,
                    trader='trader_a',
                    include_legacy_debug=True,
                    include_json_debug=True,
                )
            update_csv = reports_dir / 'trader_a_message_types_csv' / 'trader_a_update.csv'
            with update_csv.open('r', encoding='utf-8-sig', newline='') as handle:
                reader = csv.DictReader(handle)
                header = reader.fieldnames or []
                rows = list(reader)
            self.assertIn('legacy_actions', header)
            self.assertIn('normalized_json_debug', header)
            self.assertTrue(rows[0]['normalized_json_debug'].startswith('{'))
            self.assertIn('ACT_MARK_TP_HIT', rows[0]['legacy_actions'])
        finally:
            for suffix in ('', '-wal', '-shm'):
                try:
                    db_path.with_suffix(db_path.suffix + suffix).unlink()
                except (FileNotFoundError, PermissionError):
                    pass

    def test_build_report_row_handles_missing_structured_fields(self) -> None:
        row = build_report_row(
            raw_message_id=99,
            parse_status='PARSED',
            reply_to_message_id=None,
            raw_text='hello',
            warning_text=None,
            normalized={'intents': [], 'entities': {}, 'validation_warnings': []},
            scope='UNCLASSIFIED',
        )
        self.assertEqual(row['raw_message_id'], '99')
        self.assertEqual(row['action_types'], '')
        self.assertEqual(row['actions_structured_summary'], '')
        self.assertNotIn('legacy_actions', row)
        self.assertNotIn('normalized_json_debug', row)


if __name__ == '__main__':
    unittest.main()
