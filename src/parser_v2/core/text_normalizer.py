from __future__ import annotations

import re

from src.parser_v2.contracts.markers import NormalizedText


_DASH_TRANSLATION = str.maketrans({
    "–": "-",
    "—": "-",
    "−": "-",
})
_HORIZONTAL_SPACES_RE = re.compile(r"[^\S\r\n]+")


class TextNormalizer:
    def normalize(self, text: str) -> NormalizedText:
        raw_text = text
        normalized_lines = [
            line
            for line in (_normalize_line(line) for line in text.splitlines())
            if line
        ]

        return NormalizedText(
            raw_text=raw_text,
            normalized_text="\n".join(normalized_lines),
            lines=normalized_lines,
        )


def _normalize_line(line: str) -> str:
    normalized = line.translate(_DASH_TRANSLATION).lower().replace("ё", "е")
    return _HORIZONTAL_SPACES_RE.sub(" ", normalized).strip()
