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
                'CREATE TABLE raw_messages (raw_message_id INTEGER PRIMARY KEY, raw_text TEXT, reply_to_message_id INTEGER, message_ts TEXT, source_chat_id TEXT, source_chat_title TEXT, telegram_message_id TEXT, acquisition_status TEXT, acquisition_reason TEXT)'
            )
            conn.execute(
                'CREATE TABLE parse_results (raw_message_id INTEGER, resolved_trader_id TEXT, message_type TEXT, parse_status TEXT, warning_text TEXT, parse_result_normalized_json TEXT, trader_resolution_method TEXT)'
            )
            conn.executemany(
                'INSERT INTO raw_messages(raw_message_id, raw_text, reply_to_message_id, message_ts, source_chat_id, source_chat_title, telegram_message_id, acquisition_status, acquisition_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                [
                    (1, 'BTCUSDT long entry 100 sl 90 tp1 110', None, '2026-01-01T10:00:00Z', '-100', 'Trader Alpha', '1', 'ACQUIRED_ELIGIBLE', ''),
                    (2, 'TAOUSDT tp1 hit https://t.me/c/1/17', 1, '2026-01-01T10:01:00Z', '-100', 'Trader Alpha', '2', 'ACQUIRED_ELIGIBLE', ''),
                    (3, '???? ? ??', 2, '2026-01-01T10:02:00Z', '3171748254', 'PifSignal', '3', 'ACQUIRED_UNKNOWN_TRADER', 'unknown_trader'),
                    (4, '???????', 3, '2026-01-01T10:03:00Z', '3171748254', 'PifSignal', '4', 'ACQUIRED_UNKNOWN_TRADER', 'unknown_trader'),
                ],
            )
            conn.executemany(
                'INSERT INTO parse_results(raw_message_id, resolved_trader_id, message_type, parse_status, warning_text, parse_result_normalized_json, trader_resolution_method) VALUES (?, ?, ?, ?, ?, ?, ?)',
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
                        'content_alias',
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
                        'reply',
                    ),
                    (
                        3,
                        'UNRESOLVED',
                        'UNCLASSIFIED',
                        'SKIPPED',
                        '',
                        json.dumps({}, ensure_ascii=False),
                        'unresolved',
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
        self.assertEqual(row['target_refs'], '17 | 18')
        self.assertEqual(row['target_refs_count'], '2')
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
            self.assertEqual(rows[0]['target_refs_count'], '2')
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


    def test_export_reports_csv_also_writes_unresolved_messages_csv(self) -> None:
        db_path = self._make_db()
        reports_dir = Path.cwd() / 'parser_test' / 'reports'
        try:
            with patch('parser_test.reporting.report_export.load_config') as load_config:
                load_config.return_value = types.SimpleNamespace(traders={'trader_a': {}})
                updated = export_reports_csv_v2(db_path=db_path, reports_dir=reports_dir, trader='all')
            unresolved_csv = reports_dir / 'unresolved_messages.csv'
            self.assertTrue(unresolved_csv.exists())
            with unresolved_csv.open('r', encoding='utf-8-sig', newline='') as handle:
                reader = csv.DictReader(handle)
                rows = list(reader)
            self.assertTrue(any(item.scope == 'UNRESOLVED_MESSAGES' for item in updated))
            self.assertEqual([row['raw_message_id'] for row in rows], ['3', '4'])
            self.assertEqual(rows[0]['source_chat_title'], 'PifSignal')
            self.assertEqual(rows[0]['trader_resolution_method'], 'unresolved')
            self.assertEqual(rows[1]['message_type'], '')
            self.assertEqual(rows[1]['raw_text_preview'], '???????')
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

    def test_build_report_row_supports_current_router_shape(self) -> None:
        row = build_report_row(
            raw_message_id=1988,
            parse_status='PARSED',
            reply_to_message_id=None,
            raw_text='[trader#3] WLFI signal',
            warning_text='',
            normalized={
                'message_type': 'NEW_SIGNAL',
                'primary_intent': 'OPEN_POSITION',
                'intents': ['NS_CREATE_SIGNAL'],
                'confidence': 0.9,
                'target_refs': [{'kind': 'signal_id', 'ref': 2003}],
                'entities': {
                    'symbol': 'WLFIUSDT',
                    'side': 'LONG',
                    'signal_id': 2003,
                    'entry': [0.12, 0.124],
                    'entry_range_low': 0.12,
                    'entry_range_high': 0.124,
                    'take_profits': [0.13, 0.14, 0.15],
                    'stop_loss': 0.11,
                    'hashtags': ['#swing', '#tp'],
                    'links': ['https://t.me/test/1'],
                    'entry_plan_type': 'SINGLE',
                    'entry_structure': 'RANGE',
                    'has_averaging_plan': False,
                },
                'actions_structured': [
                    {
                        'action': 'OPEN_POSITION',
                        'entry_range': [0.12, 0.124],
                        'take_profits': [0.13, 0.14, 0.15],
                    }
                ],
                'warnings': [],
            },
            scope='NEW_SIGNAL',
        )
        self.assertEqual(row['signal_id'], '2003')
        self.assertEqual(row['entry_count'], '2')
        self.assertEqual(row['entries_summary'], 'RANGE_LOW:LIMIT:0.12 | RANGE_HIGH:LIMIT:0.124')
        self.assertEqual(row['tp_prices'], '0.13 | 0.14 | 0.15')
        self.assertEqual(row['stop_loss_price'], '0.11')
        self.assertEqual(row['links_count'], '1')
        self.assertEqual(row['hashtags_count'], '2')

    def test_build_report_row_prefers_entities_over_broken_canonical_numbers(self) -> None:
        row = build_report_row(
            raw_message_id=1908,
            parse_status='PARSED',
            reply_to_message_id=None,
            raw_text='BTC signal',
            warning_text='',
            normalized={
                'message_type': 'NEW_SIGNAL',
                'stop_loss_price': 97.0,
                'entities': {
                    'signal_id': 2006,
                    'symbol': 'BTCUSDT',
                    'side': 'LONG',
                    'entry': [100000.0, 101600.0],
                    'take_profits': [102000.0, 103000.0, 105000.0],
                    'stop_loss': 97000.0,
                    'entry_plan_type': 'SINGLE',
                    'entry_structure': 'RANGE',
                    'has_averaging_plan': False,
                },
                'entry_plan': {
                    'entries': [{'role': 'PRIMARY', 'order_type': 'LIMIT', 'price': 100.0}],
                    'entry_plan_type': 'SINGLE',
                    'entry_structure': 'RANGE',
                    'has_averaging_plan': False,
                },
                'take_profit_prices': [102.0, 0.0],
                'risk_plan': {
                    'stop_loss': {'price': 97.0},
                    'take_profits': [{'price': 102.0}, {'price': 0.0}],
                },
                'actions_structured': [
                    {
                        'action': 'OPEN_POSITION',
                        'entry_range': [100000.0, 101600.0],
                        'take_profits': [102000.0, 103000.0, 105000.0],
                    }
                ],
            },
            scope='NEW_SIGNAL',
        )
        self.assertEqual(row['entries_summary'], 'RANGE_LOW:LIMIT:100000 | RANGE_HIGH:LIMIT:101600')
        self.assertEqual(row['tp_prices'], '102000 | 103000 | 105000')
        self.assertEqual(row['stop_loss_price'], '97000')

    def test_build_report_row_filters_update_warning_for_new_signal(self) -> None:
        row = build_report_row(
            raw_message_id=1001,
            parse_status='PARSED',
            reply_to_message_id=None,
            raw_text='BTCUSDT long',
            warning_text='',
            normalized={
                'message_type': 'NEW_SIGNAL',
                'warnings': ['ambiguous_update_without_target', 'missing_stop_loss'],
            },
            scope='NEW_SIGNAL',
        )
        self.assertEqual(row['warnings_summary'], 'missing_stop_loss')

    def test_build_report_row_keeps_update_warning_for_ambiguous_update(self) -> None:
        row = build_report_row(
            raw_message_id=1002,
            parse_status='PARSED',
            reply_to_message_id=None,
            raw_text='Move SL now',
            warning_text='',
            normalized={
                'message_type': 'UPDATE',
                'warnings': ['ambiguous_update_without_target'],
                'target_scope': {'kind': 'signal', 'scope': 'unknown'},
            },
            scope='UPDATE',
        )
        self.assertEqual(row['warnings_summary'], 'ambiguous_update_without_target')

    def test_build_report_row_suppresses_missing_target_for_explicit_global_update(self) -> None:
        row = build_report_row(
            raw_message_id=1003,
            parse_status='PARSED',
            reply_to_message_id=None,
            raw_text='Close all longs now',
            warning_text='',
            normalized={
                'message_type': 'UPDATE',
                'warnings': ['missing_target', 'other_warning'],
                'target_scope': {'kind': 'signal_group', 'scope': 'multiple'},
            },
            scope='UPDATE',
        )
        self.assertEqual(row['warnings_summary'], 'other_warning')


if __name__ == '__main__':
    unittest.main()
