"""Integration tests for the backtesting pipeline — Step 23 Fase 7.

Covers the full pipeline end-to-end:
  a) SignalChainBuilder.build_all_async — 5 chains, correct update links
  b) ScenarioApplier.apply_all — follow_full_chain vs signals_only
  c) signal_chains.json sidecar serialisation / deserialisation
  d) BacktestRunner directory structure (freqtrade subprocess mocked)
  e) ReportGenerator: summary.json parseable as BacktestSummaryReport
  f) comparison_table_monthly.csv — monthly breakdown present
  g) gate_warn mode includes the blocked chain
  h) trader_id filter returns only the requested trader's chains
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import aiosqlite
import pytest
import pytest_asyncio

from src.backtesting.chain_builder import SignalChainBuilder
from src.backtesting.models import BacktestReadyChain
from src.backtesting.report import BacktestSummaryReport, ReportGenerator
from src.backtesting.runner import BacktestRunner, BacktestRunResult
from src.backtesting.scenario import (
    BacktestScenario,
    ScenarioApplier,
    ScenarioConditions,
)
from src.backtesting.storage import BacktestRunStore, BacktestTradeStore
from src.backtesting.tests.conftest import (
    insert_operational_signal,
    insert_parse_result,
    insert_raw_message,
    insert_signal,
    make_new_signal_json,
    make_update_json,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------

def _make_averaging_signal_json(
    *,
    symbol: str,
    direction: str,
    entry_prices: list[float],
    sl_price: float,
    tp_prices: list[float],
) -> str:
    """Build a NEW_SIGNAL normalized JSON with multiple entries (AVERAGING type)."""
    entities = {
        "symbol": symbol,
        "direction": direction,
        "entry_type": "AVERAGING",
        "entries": [
            {"price": {"raw": str(p), "value": p}, "order_type": "LIMIT"}
            for p in entry_prices
        ],
        "stop_loss": {
            "price": {"raw": str(sl_price), "value": sl_price},
            "trailing": False,
            "condition": None,
        },
        "take_profits": [
            {
                "price": {"raw": str(p), "value": p},
                "label": f"TP{i + 1}",
                "close_pct": None,
            }
            for i, p in enumerate(tp_prices)
        ],
        "leverage": None,
        "risk_pct": None,
        "conditions": None,
        "warnings": [],
    }
    return json.dumps({"message_type": "NEW_SIGNAL", "intents": [], "entities": entities})


async def _insert_new_signal(
    db: aiosqlite.Connection,
    *,
    trader_id: str,
    symbol: str,
    side: str,
    attempt_key: str,
    tg_msg_id: int,
    message_ts: str,
    normalized_json: str,
    is_blocked: bool = False,
    block_reason: str | None = None,
    source_chat_id: str = "chat_001",
) -> int:
    """Insert raw_message + parse_result + signal + op_signal for a NEW_SIGNAL.

    Returns op_signal_id.
    """
    direction = "LONG" if side == "BUY" else "SHORT"
    rm_id = await insert_raw_message(
        db,
        source_chat_id=source_chat_id,
        telegram_message_id=tg_msg_id,
        message_ts=message_ts,
        source_trader_id=trader_id,
    )
    pr_id = await insert_parse_result(
        db,
        raw_message_id=rm_id,
        message_type="NEW_SIGNAL",
        normalized_json=normalized_json,
        symbol=symbol,
        direction=direction,
    )
    await insert_signal(
        db,
        attempt_key=attempt_key,
        trader_id=trader_id,
        symbol=symbol,
        side=side,
        channel_id=source_chat_id,
    )
    return await insert_operational_signal(
        db,
        parse_result_id=pr_id,
        trader_id=trader_id,
        message_type="NEW_SIGNAL",
        attempt_key=attempt_key,
        is_blocked=is_blocked,
        block_reason=block_reason,
    )


async def _insert_update(
    db: aiosqlite.Connection,
    *,
    trader_id: str,
    tg_msg_id: int,
    message_ts: str,
    intents: list[dict[str, str]],
    target_op_id: int,
    new_sl_level: float | None = None,
    source_chat_id: str = "chat_001",
) -> int:
    """Insert raw_message + parse_result + op_signal for an UPDATE.

    Links to *target_op_id* via resolved_target_ids.  Returns op_signal_id.
    """
    normalized = make_update_json(intents=intents, new_sl_level=new_sl_level)
    rm_id = await insert_raw_message(
        db,
        source_chat_id=source_chat_id,
        telegram_message_id=tg_msg_id,
        message_ts=message_ts,
        source_trader_id=trader_id,
    )
    pr_id = await insert_parse_result(
        db,
        raw_message_id=rm_id,
        message_type="UPDATE",
        normalized_json=normalized,
    )
    return await insert_operational_signal(
        db,
        parse_result_id=pr_id,
        trader_id=trader_id,
        message_type="UPDATE",
        attempt_key=None,
        resolved_target_ids=json.dumps([target_op_id]),
    )


# ---------------------------------------------------------------------------
# five_chain_db fixture
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def five_chain_db(test_db_path: str) -> dict[str, Any]:
    """Populate the test DB with 5 predefined signal chains.

    Chain 1: LONG  BTCUSDT  trader_3   1 entry    2 TP  SL  2 updates (U_TP_HIT, U_MOVE_STOP)
    Chain 2: SHORT ETHUSDT  trader_3   avg 2 ent  3 TP  SL  1 update  (U_CLOSE_FULL)
    Chain 3: LONG  SOLUSDT  trader_3   1 entry    2 TP  SL  blocked   0 updates
    Chain 4: LONG  BTCUSDT  trader_a   1 entry    2 TP  SL  0 updates
    Chain 5: SHORT ETHUSDT  trader_a   1 entry    1 TP  SL  1 update  (U_SL_HIT)
    """
    async with aiosqlite.connect(test_db_path) as db:
        db.row_factory = aiosqlite.Row

        # ── Chain 1: LONG BTC trader_3, 2 updates ────────────────────────────
        op1 = await _insert_new_signal(
            db,
            trader_id="trader_3",
            symbol="BTCUSDT",
            side="BUY",
            attempt_key="t3_btc_001",
            tg_msg_id=101,
            message_ts="2025-01-02T10:00:00",
            normalized_json=make_new_signal_json(
                symbol="BTCUSDT",
                direction="LONG",
                entry_price=90000.0,
                sl_price=85000.0,
                tp_prices=[95000.0, 100000.0],
            ),
        )
        await _insert_update(
            db,
            trader_id="trader_3",
            tg_msg_id=102,
            message_ts="2025-01-02T12:00:00",
            intents=[{"name": "U_TP_HIT", "kind": "CONTEXT"}],
            target_op_id=op1,
        )
        await _insert_update(
            db,
            trader_id="trader_3",
            tg_msg_id=103,
            message_ts="2025-01-02T14:00:00",
            intents=[{"name": "U_MOVE_STOP", "kind": "ACTION"}],
            target_op_id=op1,
            new_sl_level=90000.0,
        )

        # ── Chain 2: SHORT ETH trader_3, averaging 2 entries, 1 update ───────
        op2 = await _insert_new_signal(
            db,
            trader_id="trader_3",
            symbol="ETHUSDT",
            side="SELL",
            attempt_key="t3_eth_001",
            tg_msg_id=201,
            message_ts="2025-01-03T10:00:00",
            normalized_json=_make_averaging_signal_json(
                symbol="ETHUSDT",
                direction="SHORT",
                entry_prices=[2900.0, 2800.0],
                sl_price=3100.0,
                tp_prices=[2700.0, 2600.0, 2500.0],
            ),
        )
        await _insert_update(
            db,
            trader_id="trader_3",
            tg_msg_id=202,
            message_ts="2025-01-03T14:00:00",
            intents=[{"name": "U_CLOSE_FULL", "kind": "ACTION"}],
            target_op_id=op2,
        )

        # ── Chain 3: LONG SOL trader_3, blocked, 0 updates ───────────────────
        op3 = await _insert_new_signal(  # noqa: F841
            db,
            trader_id="trader_3",
            symbol="SOLUSDT",
            side="BUY",
            attempt_key="t3_sol_001",
            tg_msg_id=301,
            message_ts="2025-01-04T10:00:00",
            normalized_json=make_new_signal_json(
                symbol="SOLUSDT",
                direction="LONG",
                entry_price=180.0,
                sl_price=160.0,
                tp_prices=[200.0, 220.0],
            ),
            is_blocked=True,
            block_reason="risk_too_high",
        )

        # ── Chain 4: LONG BTC trader_a, 0 updates ────────────────────────────
        op4 = await _insert_new_signal(  # noqa: F841
            db,
            trader_id="trader_a",
            symbol="BTCUSDT",
            side="BUY",
            attempt_key="ta_btc_001",
            tg_msg_id=401,
            message_ts="2025-01-05T10:00:00",
            normalized_json=make_new_signal_json(
                symbol="BTCUSDT",
                direction="LONG",
                entry_price=92000.0,
                sl_price=87000.0,
                tp_prices=[97000.0, 103000.0],
            ),
        )

        # ── Chain 5: SHORT ETH trader_a, 1 update (U_SL_HIT) ─────────────────
        op5 = await _insert_new_signal(
            db,
            trader_id="trader_a",
            symbol="ETHUSDT",
            side="SELL",
            attempt_key="ta_eth_001",
            tg_msg_id=501,
            message_ts="2025-01-06T10:00:00",
            normalized_json=make_new_signal_json(
                symbol="ETHUSDT",
                direction="SHORT",
                entry_price=2900.0,
                sl_price=3100.0,
                tp_prices=[2700.0],
            ),
        )
        await _insert_update(
            db,
            trader_id="trader_a",
            tg_msg_id=502,
            message_ts="2025-01-06T14:00:00",
            intents=[{"name": "U_SL_HIT", "kind": "CONTEXT"}],
            target_op_id=op5,
        )

    return {
        "db_path": test_db_path,
        "chain_ids": [
            "trader_3:t3_btc_001",
            "trader_3:t3_eth_001",
            "trader_3:t3_sol_001",
            "trader_a:ta_btc_001",
            "trader_a:ta_eth_001",
        ],
    }


# ---------------------------------------------------------------------------
# report_run_db fixture
# ---------------------------------------------------------------------------

_FREQTRADE_TRADES_TWO_MONTHS = [
    {
        "pair": "BTC/USDT:USDT",
        "is_short": False,
        "open_date": "2025-01-02 10:00:00",
        "close_date": "2025-01-02 18:00:00",
        "open_rate": 90000.0,
        "close_rate": 95000.0,
        "profit_abs": 50.0,
        "profit_ratio": 0.05,
        "exit_reason": "roi",
        "max_drawdown": 0.02,
        "trade_duration": 28800,
        "enter_tag": "trader_3:t3_btc_001",
    },
    {
        "pair": "ETH/USDT:USDT",
        "is_short": True,
        "open_date": "2025-02-03 10:00:00",
        "close_date": "2025-02-03 14:00:00",
        "open_rate": 2900.0,
        "close_rate": 2700.0,
        "profit_abs": 20.0,
        "profit_ratio": 0.02,
        "exit_reason": "roi",
        "max_drawdown": 0.01,
        "trade_duration": 14400,
        "enter_tag": "trader_3:t3_eth_001",
    },
]


@pytest_asyncio.fixture
async def report_run_db(test_db_path: str, tmp_path: Path) -> dict[str, Any]:
    """DB with 2 completed backtest runs and their trades inserted.

    Run 1 (follow_full_chain): 2 trades across January and February 2025.
    Run 2 (signals_only):      1 trade in January 2025.

    Returns db_path, list[BacktestRunResult], and an output report dir path.
    """
    run_store = BacktestRunStore(test_db_path)
    trade_store = BacktestTradeStore(test_db_path)

    ft_dir = tmp_path / "ft_results"
    ft_dir.mkdir()

    # ── Run 1: follow_full_chain — 2 trades (Jan + Feb) ──────────────────────
    run1_output = tmp_path / "run_follow_full_chain_001"
    run1_output.mkdir()
    run1_id = await run_store.insert_run(
        scenario_name="follow_full_chain",
        scenario_conditions_json='{"follow_full_chain": true, "signals_only": false}',
        trader_filter=None,
        date_from=None,
        date_to=None,
        chains_count=4,
        chains_blocked=1,
        output_dir=str(run1_output),
        status="COMPLETED",
    )
    results1 = ft_dir / "results1.json"
    results1.write_text(
        json.dumps({"trades": _FREQTRADE_TRADES_TWO_MONTHS}),
        encoding="utf-8",
    )
    await trade_store.import_from_freqtrade_json(run1_id, str(results1))

    # ── Run 2: signals_only — 1 trade (Jan only) ─────────────────────────────
    run2_output = tmp_path / "run_signals_only_001"
    run2_output.mkdir()
    run2_id = await run_store.insert_run(
        scenario_name="signals_only",
        scenario_conditions_json='{"follow_full_chain": false, "signals_only": true}',
        trader_filter=None,
        date_from=None,
        date_to=None,
        chains_count=4,
        chains_blocked=1,
        output_dir=str(run2_output),
        status="COMPLETED",
    )
    results2 = ft_dir / "results2.json"
    results2.write_text(
        json.dumps({"trades": [_FREQTRADE_TRADES_TWO_MONTHS[0]]}),
        encoding="utf-8",
    )
    await trade_store.import_from_freqtrade_json(run2_id, str(results2))

    run_results = [
        BacktestRunResult(
            run_id=run1_id,
            scenario_name="follow_full_chain",
            status="COMPLETED",
            output_dir=str(run1_output),
            chains_count=4,
            trades_count=2,
            freqtrade_results_path=None,  # suppress plot-profit subprocess
        ),
        BacktestRunResult(
            run_id=run2_id,
            scenario_name="signals_only",
            status="COMPLETED",
            output_dir=str(run2_output),
            chains_count=4,
            trades_count=1,
            freqtrade_results_path=None,
        ),
    ]

    return {
        "db_path": test_db_path,
        "run_results": run_results,
        "report_dir": str(tmp_path / "report"),
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_build_chains_from_db(five_chain_db: dict[str, Any]) -> None:
    """build_all_async returns 5 chains with correct ids and update linkage."""
    chains = await SignalChainBuilder.build_all_async(five_chain_db["db_path"])

    assert len(chains) == 5

    by_id = {c.chain_id: c for c in chains}
    for expected_id in five_chain_db["chain_ids"]:
        assert expected_id in by_id, f"Missing chain: {expected_id}"

    # Chain 1: 2 updates with expected intents
    c1 = by_id["trader_3:t3_btc_001"]
    assert len(c1.updates) == 2
    update_intents = {i for upd in c1.updates for i in upd.intents}
    assert "U_TP_HIT" in update_intents
    assert "U_MOVE_STOP" in update_intents

    # Chain 2: 1 update (U_CLOSE_FULL), 2 entry prices (averaging)
    c2 = by_id["trader_3:t3_eth_001"]
    assert len(c2.updates) == 1
    assert "U_CLOSE_FULL" in c2.updates[0].intents
    assert len(c2.entry_prices) == 2

    # Chain 3: blocked, 0 updates
    c3 = by_id["trader_3:t3_sol_001"]
    assert c3.new_signal.is_blocked is True
    assert c3.updates == []

    # Chain 4: trader_a, 0 updates
    c4 = by_id["trader_a:ta_btc_001"]
    assert c4.trader_id == "trader_a"
    assert c4.updates == []

    # Chain 5: 1 update (U_SL_HIT)
    c5 = by_id["trader_a:ta_eth_001"]
    assert len(c5.updates) == 1
    assert "U_SL_HIT" in c5.updates[0].intents


async def test_apply_two_scenarios(five_chain_db: dict[str, Any]) -> None:
    """follow_full_chain excludes the blocked chain; signals_only clears applied_updates."""
    chains = await SignalChainBuilder.build_all_async(five_chain_db["db_path"])

    scenario_full = BacktestScenario(
        name="follow_full_chain",
        description="Follow all updates",
        conditions=ScenarioConditions(follow_full_chain=True, signals_only=False),
    )
    scenario_signals_only = BacktestScenario(
        name="signals_only",
        description="Original signals only",
        conditions=ScenarioConditions(follow_full_chain=False, signals_only=True),
    )

    # follow_full_chain: Chain 3 (blocked) is excluded → 4 chains
    ready_full = ScenarioApplier.apply_all(chains, scenario_full)
    assert len(ready_full) == 4
    for rc in ready_full:
        assert rc.chain.new_signal.is_blocked is False

    full_by_id = {rc.chain.chain_id: rc for rc in ready_full}
    assert len(full_by_id["trader_3:t3_btc_001"].applied_updates) == 2
    assert len(full_by_id["trader_3:t3_eth_001"].applied_updates) == 1
    assert len(full_by_id["trader_a:ta_eth_001"].applied_updates) == 1

    # signals_only: 4 chains, all applied_updates empty
    ready_signals = ScenarioApplier.apply_all(chains, scenario_signals_only)
    assert len(ready_signals) == 4
    for rc in ready_signals:
        assert rc.applied_updates == []


async def test_sidecar_json_written(five_chain_db: dict[str, Any], tmp_path: Path) -> None:
    """signal_chains.json can be written and round-tripped as BacktestReadyChain."""
    chains = await SignalChainBuilder.build_all_async(five_chain_db["db_path"])

    scenario = BacktestScenario(
        name="follow_full_chain",
        description="Full chain",
        conditions=ScenarioConditions(follow_full_chain=True, signals_only=False),
    )
    ready = ScenarioApplier.apply_all(chains, scenario)

    sidecar = tmp_path / "signal_chains.json"
    sidecar.write_text(
        json.dumps(
            [c.model_dump(mode="json") for c in ready],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    assert sidecar.exists()
    loaded = json.loads(sidecar.read_text(encoding="utf-8"))
    assert isinstance(loaded, list)
    assert len(loaded) == len(ready)

    for item in loaded:
        parsed = BacktestReadyChain.model_validate(item)
        assert parsed.scenario_name == "follow_full_chain"


async def test_run_directory_structure(five_chain_db: dict[str, Any], tmp_path: Path) -> None:
    """BacktestRunner creates run_*/ with signal_chains.json and freqtrade_config.json."""
    scenario_yaml = tmp_path / "scenarios.yaml"
    scenario_yaml.write_text(
        """\
backtest_settings:
  exchange: bybit
  timeframe: 5m
  capital_base_usdt: 1000.0
  max_open_trades: 10
scenarios:
  - name: follow_full_chain
    description: "Follow all updates"
    conditions:
      follow_full_chain: true
      signals_only: false
""",
        encoding="utf-8",
    )

    output_base = tmp_path / "reports"
    runner = BacktestRunner(db_path=five_chain_db["db_path"], output_base=str(output_base))

    with patch.object(runner, "_run_freqtrade", return_value=(0, "OK", "")):
        results = await runner.run(str(scenario_yaml))

    assert len(results) == 1
    result = results[0]
    assert result.scenario_name == "follow_full_chain"

    run_dir = Path(result.output_dir)
    assert run_dir.exists()

    chains_file = run_dir / "signal_chains.json"
    config_file = run_dir / "freqtrade_config.json"
    assert chains_file.exists(), "signal_chains.json not found in run directory"
    assert config_file.exists(), "freqtrade_config.json not found in run directory"

    chains_data = json.loads(chains_file.read_text(encoding="utf-8"))
    assert isinstance(chains_data, list)
    assert len(chains_data) > 0  # 4 chains (Chain 3 blocked excluded)

    ft_config = json.loads(config_file.read_text(encoding="utf-8"))
    assert "pairs" in ft_config
    assert "strategy_params" in ft_config
    assert "signal_chains_path" in ft_config["strategy_params"]


async def test_report_summary_parseable(report_run_db: dict[str, Any]) -> None:
    """ReportGenerator produces a summary.json parseable as BacktestSummaryReport.

    comparison_table.csv has one data row per scenario in insertion order.
    """
    db_path = report_run_db["db_path"]
    run_results: list[BacktestRunResult] = report_run_db["run_results"]
    report_dir = report_run_db["report_dir"]

    generator = ReportGenerator(db_path=db_path)
    report = await generator.generate(run_results, report_dir)

    # summary.json is present and parseable
    summary_path = Path(report_dir) / "summary.json"
    assert summary_path.exists()
    parsed = BacktestSummaryReport.model_validate_json(
        summary_path.read_text(encoding="utf-8")
    )
    assert len(parsed.scenarios) == 2
    scenario_names = {s.scenario_name for s in parsed.scenarios}
    assert "follow_full_chain" in scenario_names
    assert "signals_only" in scenario_names

    # comparison_table.csv: header + 2 scenario rows
    csv_path = Path(report_dir) / "comparison_table.csv"
    assert csv_path.exists()
    lines = csv_path.read_text(encoding="utf-8-sig").strip().splitlines()
    assert len(lines) == 3  # header + 2 data rows
    assert "follow_full_chain" in lines[1]
    assert "signals_only" in lines[2]

    # Returned report matches the parsed summary
    assert report.scenarios == parsed.scenarios


async def test_comparison_table_monthly_present(report_run_db: dict[str, Any]) -> None:
    """comparison_table_monthly.csv exists and contains multi-month breakdown."""
    db_path = report_run_db["db_path"]
    run_results: list[BacktestRunResult] = report_run_db["run_results"]
    report_dir = report_run_db["report_dir"]

    generator = ReportGenerator(db_path=db_path)
    await generator.generate(run_results, report_dir)

    monthly_csv = Path(report_dir) / "comparison_table_monthly.csv"
    assert monthly_csv.exists()

    lines = monthly_csv.read_text(encoding="utf-8-sig").strip().splitlines()
    # header + at least 3 rows (follow_full_chain:2025-01, 2025-02; signals_only:2025-01)
    assert len(lines) >= 4
    header = lines[0]
    assert "scenario" in header
    assert "month" in header

    # Both months represented in the data rows
    data = "\n".join(lines[1:])
    assert "2025-01" in data
    assert "2025-02" in data


async def test_gate_warn_includes_blocked(five_chain_db: dict[str, Any]) -> None:
    """gate_mode_variant='warn' includes the blocked chain with include_blocked=True."""
    chains = await SignalChainBuilder.build_all_async(five_chain_db["db_path"])

    scenario_gate_warn = BacktestScenario(
        name="gate_warn",
        description="Include blocked chains as warnings",
        conditions=ScenarioConditions(
            follow_full_chain=True,
            signals_only=False,
            gate_mode_variant="warn",
        ),
    )

    ready = ScenarioApplier.apply_all(chains, scenario_gate_warn)
    assert len(ready) == 5  # all 5 chains, including Chain 3 (blocked)

    blocked_chains = [rc for rc in ready if rc.chain.new_signal.is_blocked]
    assert len(blocked_chains) == 1
    assert blocked_chains[0].chain.chain_id == "trader_3:t3_sol_001"
    assert blocked_chains[0].include_blocked is True


async def test_filter_by_trader(five_chain_db: dict[str, Any]) -> None:
    """build_all_async with trader_id='trader_3' returns only the 3 trader_3 chains."""
    chains = await SignalChainBuilder.build_all_async(
        five_chain_db["db_path"], trader_id="trader_3"
    )

    assert len(chains) == 3
    for chain in chains:
        assert chain.trader_id == "trader_3"

    chain_ids = {c.chain_id for c in chains}
    assert "trader_3:t3_btc_001" in chain_ids
    assert "trader_3:t3_eth_001" in chain_ids
    assert "trader_3:t3_sol_001" in chain_ids
