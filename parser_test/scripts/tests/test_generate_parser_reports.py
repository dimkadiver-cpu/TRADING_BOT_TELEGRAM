from __future__ import annotations

from types import SimpleNamespace

from parser_test.scripts import generate_parser_reports as module


def test_generate_parser_reports_passes_parser_system_flag(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_replay_database(**kwargs: object) -> None:
        captured.update(kwargs)

    def fake_export_reports_csv_v2(**kwargs: object) -> list[object]:
        captured["export"] = kwargs
        return []

    monkeypatch.setattr(module, "replay_database", fake_replay_database)
    monkeypatch.setattr(module, "export_reports_csv_v2", fake_export_reports_csv_v2)
    monkeypatch.setattr(module, "_print_summary", lambda updated: None)
    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: SimpleNamespace(
            db_path="C:\\TeleSignalBot\\parser_test\\db\\parser_test__chat_1003171748254.sqlite3",
            db_name=None,
            db_per_chat=False,
            trader="trader_a",
            only_unparsed=False,
            limit=None,
            chat_id=None,
            from_date=None,
            to_date=None,
            parser_mode=None,
            show_normalized_samples=3,
            reports_dir="parser_test/reports",
            include_legacy_debug=False,
            include_json_debug=False,
            parser_system="parsed_message",
        ),
    )

    module.main()

    assert captured["parser_system"] == "parsed_message"
    assert captured["trader"] == "trader_a"
    assert "export" in captured

