from __future__ import annotations

import os
from pathlib import Path

from src.parser_v2.core.profile_assets import load_markers_cached, load_rules_cached


def _write_assets(profile_dir: Path, *, default_entry_type: str | None = None) -> None:
    rules: dict = {}
    if default_entry_type is not None:
        rules = {"default_entry_type": default_entry_type}
    import json

    (profile_dir / "rules.json").write_text(json.dumps(rules), encoding="utf-8")
    (profile_dir / "semantic_markers.json").write_text("{}", encoding="utf-8")


def test_load_rules_cached_returns_same_instance_for_unchanged_file(tmp_path: Path) -> None:
    _write_assets(tmp_path)

    first = load_rules_cached(tmp_path)
    second = load_rules_cached(tmp_path)

    assert first is second


def test_load_markers_cached_returns_same_instance_for_unchanged_file(tmp_path: Path) -> None:
    _write_assets(tmp_path)

    first = load_markers_cached(tmp_path)
    second = load_markers_cached(tmp_path)

    assert first is second


def test_load_rules_cached_reloads_when_file_changes(tmp_path: Path) -> None:
    _write_assets(tmp_path)
    stale = load_rules_cached(tmp_path)
    assert stale.default_entry_type is None

    _write_assets(tmp_path, default_entry_type="MARKET")
    # forza un mtime diverso anche su filesystem a bassa risoluzione
    rules_path = tmp_path / "rules.json"
    st = rules_path.stat()
    os.utime(rules_path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))

    fresh = load_rules_cached(tmp_path)

    assert fresh is not stale
    assert fresh.default_entry_type == "MARKET"
