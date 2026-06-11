from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from src.parser_v2.contracts.rules import ParserRules, SemanticMarkers


def load_rules_cached(profile_dir: Path) -> ParserRules:
    path = profile_dir / "rules.json"
    return _load_rules(str(path), path.stat().st_mtime_ns)


def load_markers_cached(profile_dir: Path) -> SemanticMarkers:
    path = profile_dir / "semantic_markers.json"
    return _load_markers(str(path), path.stat().st_mtime_ns)


# La chiave include mtime_ns: una modifica al file invalida la entry e il watch
# mode continua a vedere i JSON aggiornati. Le istanze restituite sono condivise
# tra le chiamate: i chiamanti non devono mutarle (usare model_copy se serve).


@lru_cache(maxsize=32)
def _load_rules(path: str, mtime_ns: int) -> ParserRules:
    return ParserRules.model_validate_json(Path(path).read_text(encoding="utf-8"))


@lru_cache(maxsize=32)
def _load_markers(path: str, mtime_ns: int) -> SemanticMarkers:
    return SemanticMarkers.model_validate_json(Path(path).read_text(encoding="utf-8"))


__all__ = ["load_rules_cached", "load_markers_cached"]
