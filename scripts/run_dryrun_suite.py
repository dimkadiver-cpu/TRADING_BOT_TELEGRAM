r"""Run a suite of synthetic Telegram scenarios against the dry-run stack.

This runner automates the repetitive loop:
1. optionally reset bot/freqtrade DBs
2. inject scenario files in dependency order
3. wait for the bridge runtime to react
4. verify expected outcome on bot DB + freqtrade dry-run DB

Typical usage:
    C:\TeleSignalBot\.venv\Scripts\python.exe C:\TeleSignalBot\scripts\run_dryrun_suite.py ^
      --scenario-dir C:\TeleSignalBot\scripts\trader_a_scenarios --reset
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.verify_attempt_expectation import VERIFY_MAP  # type: ignore[import-not-found]
from scripts.inspect_attempt import (  # type: ignore[import-not-found]
    _connect,
    load_attempt_snapshot,
    load_freqtrade_snapshot,
)

DEFAULT_SCENARIO_DIR = PROJECT_ROOT / "scripts" / "trader_a_scenarios"
DEFAULT_BOT_DB = PROJECT_ROOT / "db" / "tele_signal_bot.sqlite3"
DEFAULT_FREQTRADE_DB = PROJECT_ROOT / "freqtrade" / "tradesv3.dryrun.sqlite"

DEFAULT_EXPECT_BY_STEM = {
    "u01_move_stop_to_be": "move_stop",
    "u02_move_stop_to_level": "move_stop",
    "u03_tp1_hit_stop_to_be": "tp1",
    "u04_tp2_hit_close_rest": "close_full",
    "u05_stop_hit": "close_full",
    "u06_close_partial_50": "close_partial",
    "u07_close_full": "close_full",
    "u08_cancel_pending": "cancel_pending",
    "u10_mark_filled": "entry_filled",
}


@dataclass(slots=True)
class ScenarioFile:
    path: Path
    messages: list[dict[str, Any]]
    name: str
    first_message_id: int | None
    reply_to_message_ids: list[int]
    explicit_expect: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Automate injection + dry-run verification for a suite of synthetic scenarios."
    )
    parser.add_argument(
        "--scenario-dir",
        default=str(DEFAULT_SCENARIO_DIR),
        help="Directory containing JSON scenario files.",
    )
    parser.add_argument(
        "--files",
        nargs="*",
        default=None,
        help="Specific JSON filenames to run. Dependencies are included automatically.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset bot DB and freqtrade dry-run DB before the suite.",
    )
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_BOT_DB),
        help="TeleSignalBot SQLite DB path.",
    )
    parser.add_argument(
        "--freqtrade-db-path",
        default=str(DEFAULT_FREQTRADE_DB),
        help="Freqtrade dry-run SQLite DB path.",
    )
    parser.add_argument(
        "--inject-wait-seconds",
        type=float,
        default=0.5,
        help="Small pause after each injection.",
    )
    parser.add_argument(
        "--verify-timeout-seconds",
        type=float,
        default=8.0,
        help="How long to poll for the expected dry-run outcome.",
    )
    parser.add_argument(
        "--verify-interval-seconds",
        type=float,
        default=1.0,
        help="Polling interval between verification attempts.",
    )
    parser.add_argument(
        "--report-json",
        default=None,
        help="Optional path to save the final JSON report.",
    )
    parser.add_argument(
        "--no-stop-on-fail",
        action="store_true",
        help="Continue with later scenarios even if one case fails.",
    )
    return parser.parse_args()


def _python_executable() -> str:
    return sys.executable


def _run_subprocess(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _load_json_file(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, dict):
        items = [payload]
    elif isinstance(payload, list):
        items = payload
    else:
        raise SystemExit(f"Scenario file must contain an object or a list: {path}")
    out: list[dict[str, Any]] = []
    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise SystemExit(f"Scenario item #{idx} is not an object in {path}")
        out.append(item)
    return out


def _scenario_name(path: Path, messages: list[dict[str, Any]]) -> str:
    for item in messages:
        value = item.get("name")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return path.stem


def _first_message_id(messages: list[dict[str, Any]]) -> int | None:
    for item in messages:
        raw = item.get("telegram_message_id")
        if raw is not None:
            return int(raw)
    return None


def _reply_to_ids(messages: list[dict[str, Any]]) -> list[int]:
    return [int(item["reply_to_message_id"]) for item in messages if item.get("reply_to_message_id") is not None]


def _explicit_expect(messages: list[dict[str, Any]], stem: str) -> str | None:
    for item in messages:
        value = item.get("expect")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return DEFAULT_EXPECT_BY_STEM.get(stem)


def load_scenarios(scenario_dir: Path) -> dict[str, ScenarioFile]:
    files = sorted(scenario_dir.glob("*.json"))
    if not files:
        raise SystemExit(f"No scenario files found in {scenario_dir}")
    scenarios: dict[str, ScenarioFile] = {}
    for path in files:
        messages = _load_json_file(path)
        scenarios[path.name] = ScenarioFile(
            path=path,
            messages=messages,
            name=_scenario_name(path, messages),
            first_message_id=_first_message_id(messages),
            reply_to_message_ids=_reply_to_ids(messages),
            explicit_expect=_explicit_expect(messages, path.stem),
        )
    return scenarios


def build_execution_plan(
    *,
    scenarios: dict[str, ScenarioFile],
    selected_files: list[str] | None,
) -> list[ScenarioFile]:
    by_message_id = {
        scenario.first_message_id: scenario
        for scenario in scenarios.values()
        if scenario.first_message_id is not None
    }
    selected = list(selected_files) if selected_files else sorted(scenarios.keys())
    for name in selected:
        if name not in scenarios:
            raise SystemExit(f"Scenario file not found: {name}")

    planned: list[ScenarioFile] = []
    added: set[str] = set()

    def visit(name: str) -> None:
        if name in added:
            return
        scenario = scenarios[name]
        for parent_message_id in scenario.reply_to_message_ids:
            parent = by_message_id.get(parent_message_id)
            if parent is not None:
                visit(parent.path.name)
        planned.append(scenario)
        added.add(name)

    for name in selected:
        visit(name)

    return planned


def reset_databases() -> None:
    command = [_python_executable(), str(PROJECT_ROOT / "scripts" / "reset_live_db.py")]
    result = _run_subprocess(command)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "reset_live_db.py failed")
    if result.stdout.strip():
        print(result.stdout.strip())


def inject_scenario(*, scenario: ScenarioFile, db_path: Path) -> dict[str, Any]:
    command = [
        _python_executable(),
        str(PROJECT_ROOT / "scripts" / "inject_fake_messages.py"),
        "--db-path",
        str(db_path),
        "--chat-id",
        str(scenario.messages[0].get("chat_id") or "-100111"),
        "--trader",
        str(scenario.messages[0].get("trader") or scenario.messages[0].get("trader_id") or ""),
        "--scenario-file",
        str(scenario.path),
    ]
    result = _run_subprocess(command)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"injection failed for {scenario.path.name}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Injection output is not valid JSON for {scenario.path.name}: {exc}") from exc


def _attempt_key_from_injection(payload: dict[str, Any]) -> str | None:
    for item in payload.get("results") or []:
        signal = item.get("signal") or {}
        value = signal.get("attempt_key")
        if isinstance(value, str) and value.strip():
            return value
        operational = item.get("operational_signal") or {}
        value = operational.get("attempt_key")
        if isinstance(value, str) and value.strip():
            return value
    return None


def _run_expectation_once(*, db_path: Path, freqtrade_db_path: Path, attempt_key: str, expect: str) -> tuple[bool, list[dict[str, Any]]]:
    with _connect(str(db_path)) as conn:
        snapshot = load_attempt_snapshot(conn=conn, attempt_key=attempt_key)
    freqtrade = load_freqtrade_snapshot(
        freqtrade_db_path=str(freqtrade_db_path),
        attempt_key=attempt_key,
    )
    checks = VERIFY_MAP[expect](snapshot, freqtrade)
    serialized = [
        {"name": check.name, "ok": check.ok, "detail": check.detail}
        for check in checks
    ]
    return all(item["ok"] for item in serialized), serialized


def verify_with_polling(
    *,
    db_path: Path,
    freqtrade_db_path: Path,
    attempt_key: str,
    expect: str,
    timeout_seconds: float,
    interval_seconds: float,
) -> tuple[bool, list[dict[str, Any]]]:
    deadline = time.monotonic() + timeout_seconds
    last_checks: list[dict[str, Any]] = []
    while True:
        ok, checks = _run_expectation_once(
            db_path=db_path,
            freqtrade_db_path=freqtrade_db_path,
            attempt_key=attempt_key,
            expect=expect,
        )
        last_checks = checks
        if ok:
            return True, checks
        if time.monotonic() >= deadline:
            return False, last_checks
        time.sleep(interval_seconds)


def main() -> int:
    args = parse_args()
    scenario_dir = Path(args.scenario_dir).resolve()
    db_path = Path(args.db_path).resolve()
    freqtrade_db_path = Path(args.freqtrade_db_path).resolve()
    scenarios = load_scenarios(scenario_dir)
    plan = build_execution_plan(scenarios=scenarios, selected_files=args.files)

    if args.reset:
        print("== RESET ==")
        reset_databases()
        print("")

    print("== PLAN ==")
    for scenario in plan:
        expect = scenario.explicit_expect or "-"
        print(f"- {scenario.path.name}  expect={expect}")
    print("")

    report: dict[str, Any] = {
        "ok": True,
        "scenario_dir": str(scenario_dir),
        "db_path": str(db_path),
        "freqtrade_db_path": str(freqtrade_db_path),
        "results": [],
    }

    for scenario in plan:
        print(f"== RUN {scenario.path.name} ==")
        payload = inject_scenario(scenario=scenario, db_path=db_path)
        attempt_key = _attempt_key_from_injection(payload)
        duplicate = any(
            bool((row.get("injection") or {}).get("duplicate_raw_message"))
            for row in (payload.get("results") or [])
        )
        scenario_result: dict[str, Any] = {
            "file": scenario.path.name,
            "name": scenario.name,
            "expect": scenario.explicit_expect,
            "attempt_key": attempt_key,
            "duplicate_raw_message": duplicate,
            "injection": payload,
            "verification": None,
        }

        if args.inject_wait_seconds > 0:
            time.sleep(args.inject_wait_seconds)

        if scenario.explicit_expect and attempt_key:
            passed, checks = verify_with_polling(
                db_path=db_path,
                freqtrade_db_path=freqtrade_db_path,
                attempt_key=attempt_key,
                expect=scenario.explicit_expect,
                timeout_seconds=args.verify_timeout_seconds,
                interval_seconds=args.verify_interval_seconds,
            )
            scenario_result["verification"] = {
                "expect": scenario.explicit_expect,
                "passed": passed,
                "checks": checks,
            }
            status = "PASS" if passed else "FAIL"
            print(f"{status} {scenario.explicit_expect} attempt_key={attempt_key}")
            for check in checks:
                check_status = "PASS" if check["ok"] else "FAIL"
                print(f"  [{check_status}] {check['name']} - {check['detail']}")
            if not passed:
                report["ok"] = False
                if not args.no_stop_on_fail:
                    report["results"].append(scenario_result)
                    break
        elif scenario.explicit_expect and not attempt_key:
            scenario_result["verification"] = {
                "expect": scenario.explicit_expect,
                "passed": False,
                "checks": [{"name": "attempt_key_present", "ok": False, "detail": "No attempt_key produced by injection"}],
            }
            report["ok"] = False
            print(f"FAIL {scenario.explicit_expect} attempt_key=<missing>")
            if not args.no_stop_on_fail:
                report["results"].append(scenario_result)
                break
        else:
            print(f"INFO no verification mapped for {scenario.path.name}")

        report["results"].append(scenario_result)
        print("")

    if args.report_json:
        report_path = Path(args.report_json).resolve()
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Report JSON written to {report_path}")

    passed = sum(1 for item in report["results"] if (item.get("verification") or {}).get("passed") is True)
    failed = sum(1 for item in report["results"] if (item.get("verification") or {}).get("passed") is False)
    skipped = sum(1 for item in report["results"] if item.get("verification") is None)

    print("== SUMMARY ==")
    print(f"Suite result: {'PASS' if report['ok'] else 'FAIL'}")
    print(f"Verified passed: {passed}")
    print(f"Verified failed: {failed}")
    print(f"No-check cases: {skipped}")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
