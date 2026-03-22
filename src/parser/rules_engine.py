"""RulesEngine — classifica messaggi e rileva intents tramite parsing_rules.json.

Uso:
    engine = RulesEngine.load("src/parser/trader_profiles/trader_X/parsing_rules.json")
    result = engine.classify(text)
    intents = engine.detect_intents(text)

Il RulesEngine legge il file JSON del profilo, fa merge con il vocabolario condiviso
(se dichiarato), e applica i marcatori per classificare il messaggio e rilevare intents.

Non contiene logica di estrazione entità — quella appartiene al profile.py specifico.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

# Pesi dei marcatori per il calcolo del confidence score.
_STRONG_WEIGHT: float = 1.0
_WEAK_WEIGHT: float = 0.4

# Cartella dei vocabolari condivisi, relativa a questo file.
_SHARED_DIR = Path(__file__).resolve().parent / "trader_profiles" / "shared"

# Categorie di classificazione riconosciute.
_CATEGORIES: tuple[str, ...] = ("new_signal", "update", "info_only")

MessageType = Literal["NEW_SIGNAL", "UPDATE", "INFO_ONLY", "UNCLASSIFIED"]


# ---------------------------------------------------------------------------
# ClassificationResult
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ClassificationResult:
    """Output della classificazione prodotto da RulesEngine.classify().

    message_type:    categoria vincente o UNCLASSIFIED se nessun marcatore ha
                     trovato corrispondenza.
    confidence:      float 0.0–1.0 che riflette la forza dei segnali trovati.
    matched_markers: lista dei marcatori che hanno generato una corrispondenza
                     nel testo, formato "<categoria>/<marker>".
    intents_hint:    intents rilevati da intent_markers — suggerimento per il
                     profile.py, non sostituto della sua logica.
    """

    message_type: MessageType
    confidence: float
    matched_markers: list[str] = field(default_factory=list)
    intents_hint: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# RulesEngine
# ---------------------------------------------------------------------------

class RulesEngine:
    """Motore di regole basato su parsing_rules.json.

    Responsabilità:
        - Caricare il JSON del profilo e fare merge con il vocabolario condiviso.
        - Classificare un testo in NEW_SIGNAL | UPDATE | INFO_ONLY | UNCLASSIFIED.
        - Rilevare intents UPDATE tramite intent_markers.
        - Esporre number_format per l'uso nel profile.py (normalizzazione Price).

    NON di competenza:
        - Estrazione entità (symbol, entries, SL, TP, …).
        - Logica condizionale specifica del trader.
        - Accesso al DB o al contesto Telegram.
    """

    def __init__(self, rules: dict[str, Any]) -> None:
        """Costruttore interno — usa RulesEngine.load() per creare un'istanza."""
        self._rules = rules
        self._classification_markers = _normalise_classification_markers(
            rules.get("classification_markers", {})
        )
        self._intent_markers: dict[str, list[str]] = rules.get("intent_markers", {})
        self._combination_rules: list[dict[str, Any]] = rules.get("combination_rules", [])
        self._blacklist: list[str] = rules.get("blacklist", [])

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> RulesEngine:
        """Carica un RulesEngine dal parsing_rules.json del profilo.

        Se il JSON dichiara `shared_vocabulary`, fa merge automatico con il
        corrispondente file JSON in trader_profiles/shared/.  Il profilo ha
        sempre precedenza in caso di chiave duplicata.

        Args:
            path: Percorso al parsing_rules.json del profilo.

        Returns:
            Istanza inizializzata di RulesEngine.

        Raises:
            FileNotFoundError: Se il file del profilo non esiste.
            json.JSONDecodeError: Se il JSON non è valido.
        """
        path = Path(path)
        with path.open(encoding="utf-8") as fh:
            profile_rules: dict[str, Any] = json.load(fh)

        shared_name = profile_rules.get("shared_vocabulary")
        if shared_name:
            shared_path = _SHARED_DIR / f"{shared_name}.json"
            if shared_path.exists():
                with shared_path.open(encoding="utf-8") as fh:
                    shared_rules: dict[str, Any] = json.load(fh)
                merged = _merge_rules(base=shared_rules, override=profile_rules)
            else:
                logger.warning(
                    "shared_vocabulary %r declared but file not found: %s",
                    shared_name,
                    shared_path,
                )
                merged = profile_rules
        else:
            merged = profile_rules

        return cls(merged)

    @classmethod
    def from_dict(cls, rules: dict[str, Any]) -> RulesEngine:
        """Crea un RulesEngine direttamente da un dict (comodo nei test)."""
        return cls(rules)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify(self, text: str) -> ClassificationResult:
        """Classifica il testo usando classification_markers e combination_rules.

        Algoritmo:
            1. Normalizza il testo (lowercase, strip).
            2. Per ogni categoria: somma strong × 1.0 + weak × 0.4.
            3. Applica combination_rules: se tutti i marker "if" sono presenti,
               aggiunge confidence_boost alla categoria "then".
            4. Categoria con score più alto vince.
            5. Se nessun marker corrisponde → UNCLASSIFIED, confidence=0.0.

        Args:
            text: Testo grezzo del messaggio.

        Returns:
            ClassificationResult con message_type, confidence e matched_markers.
        """
        normalized = _normalise_text(text)
        scores: dict[str, float] = {cat: 0.0 for cat in _CATEGORIES}
        matched: list[str] = []

        # --- 1. Applica classification_markers ---
        for cat in _CATEGORIES:
            markers_def = self._classification_markers.get(cat, {"strong": [], "weak": []})
            for marker in markers_def.get("strong", []):
                if marker in normalized:
                    scores[cat] += _STRONG_WEIGHT
                    matched.append(f"{cat}/{marker}")
            for marker in markers_def.get("weak", []):
                if marker in normalized:
                    scores[cat] += _WEAK_WEIGHT
                    matched.append(f"{cat}/{marker}")

        # --- 2. Applica combination_rules ---
        for rule in self._combination_rules:
            if_markers: list[str] = rule.get("if", [])
            then_target: str = rule.get("then", "")
            boost: float = float(rule.get("confidence_boost", 0.0))

            if not if_markers or not then_target:
                continue
            if all(_normalise_text(m) in normalized for m in if_markers):
                # "then" può essere una categoria o un intent — boost solo categorie
                if then_target in scores:
                    scores[then_target] += boost

        # --- 3. Determina il vincitore ---
        intents_hint = self.detect_intents(text)
        best_cat, best_score = max(scores.items(), key=lambda kv: kv[1])

        if best_score == 0.0:
            return ClassificationResult(
                message_type="UNCLASSIFIED",
                confidence=0.0,
                matched_markers=matched,
                intents_hint=intents_hint,
            )

        message_type: MessageType = _category_to_message_type(best_cat)
        confidence = min(1.0, best_score)

        return ClassificationResult(
            message_type=message_type,
            confidence=confidence,
            matched_markers=matched,
            intents_hint=intents_hint,
        )

    def detect_intents(self, text: str) -> list[str]:
        """Rileva intents UPDATE tramite intent_markers.

        Ogni intent per cui almeno un marker corrisponde nel testo viene
        incluso nel risultato, in ordine di dichiarazione.

        Args:
            text: Testo grezzo del messaggio.

        Returns:
            Lista di nomi intent (es. ["U_CLOSE_FULL", "U_MOVE_STOP"]).
        """
        normalized = _normalise_text(text)
        found: list[str] = []
        for intent_name, markers in self._intent_markers.items():
            for marker in markers:
                if _normalise_text(marker) in normalized:
                    found.append(intent_name)
                    break  # un solo match per intent è sufficiente
        return found

    def is_blacklisted(self, text: str) -> bool:
        """Restituisce True se il testo contiene un marker blacklist."""
        normalized = _normalise_text(text)
        return any(_normalise_text(m) in normalized for m in self._blacklist)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def number_format(self) -> dict[str, str | None]:
        """Configurazione formato numerico dichiarata nel profilo.

        Restituisce sempre un dict con le chiavi `decimal_separator` e
        `thousands_separator`.  I valori di default sono "." e None.
        """
        nf = self._rules.get("number_format", {})
        return {
            "decimal_separator": str(nf.get("decimal_separator", ".")),
            "thousands_separator": nf.get("thousands_separator") or None,
        }

    @property
    def language(self) -> str | None:
        """Lingua dichiarata nel profilo ("ru", "en", "it", …)."""
        return self._rules.get("language")

    @property
    def fallback_hook_enabled(self) -> bool:
        """True se il profilo dichiara un LLM fallback hook abilitato."""
        return bool(
            self._rules.get("fallback_hook", {}).get("enabled", False)
        )

    @property
    def raw_rules(self) -> dict[str, Any]:
        """Dict grezzo del profilo — uso di emergenza/debug."""
        return dict(self._rules)


# ---------------------------------------------------------------------------
# Funzioni private di utilità
# ---------------------------------------------------------------------------

def _normalise_text(text: str) -> str:
    """Lowercase + strip per il matching dei marcatori."""
    return text.lower().strip()


def _normalise_classification_markers(
    raw: dict[str, Any],
) -> dict[str, dict[str, list[str]]]:
    """Normalizza classification_markers verso il formato canonico.

    Supporta due formati in input:

    Nuovo (PRD):
        { "new_signal": { "strong": [...], "weak": [...] }, ... }

    Legacy (flat list, trattato come strong):
        { "new_signal": ["marker1", "marker2"], ... }

    Restituisce sempre il formato nuovo.
    """
    result: dict[str, dict[str, list[str]]] = {}
    for cat, value in raw.items():
        if isinstance(value, list):
            result[cat] = {"strong": [m.lower() for m in value], "weak": []}
        elif isinstance(value, dict):
            result[cat] = {
                "strong": [m.lower() for m in value.get("strong", [])],
                "weak": [m.lower() for m in value.get("weak", [])],
            }
        else:
            logger.warning("classification_markers[%r]: formato non riconosciuto", cat)
    return result


def _merge_rules(
    base: dict[str, Any],
    override: dict[str, Any],
) -> dict[str, Any]:
    """Merge profilo + vocabolario condiviso.

    Strategia:
        - Campi scalari (language, number_format, …): override vince.
        - Campi lista (blacklist): unione con deduplicazione.
        - classification_markers: unione per categoria e per strong/weak,
          deduplicazione, override vince su valori duplicati.
        - intent_markers / target_ref_markers: unione per chiave, deduplicazione.
    """
    merged: dict[str, Any] = dict(base)

    for key, override_val in override.items():
        if key not in merged:
            merged[key] = override_val
            continue

        base_val = merged[key]

        if key in ("classification_markers",):
            merged[key] = _merge_classification_markers(base_val, override_val)
        elif key in ("intent_markers", "target_ref_markers"):
            merged[key] = _merge_dict_of_lists(base_val, override_val)
        elif key == "blacklist" and isinstance(base_val, list) and isinstance(override_val, list):
            seen: set[str] = set()
            merged_list: list[str] = []
            for item in (*base_val, *override_val):
                if item not in seen:
                    seen.add(item)
                    merged_list.append(item)
            merged[key] = merged_list
        else:
            # Scalari e dict semplici: override vince
            merged[key] = override_val

    return merged


def _merge_classification_markers(
    base: dict[str, Any],
    override: dict[str, Any],
) -> dict[str, Any]:
    """Merge di due classification_markers dict — unione per categoria."""
    base_norm = _normalise_classification_markers(base)
    over_norm = _normalise_classification_markers(override)
    result: dict[str, Any] = dict(base_norm)
    for cat, over_def in over_norm.items():
        if cat not in result:
            result[cat] = over_def
        else:
            result[cat] = {
                "strong": _dedup(result[cat].get("strong", []) + over_def.get("strong", [])),
                "weak": _dedup(result[cat].get("weak", []) + over_def.get("weak", [])),
            }
    return result


def _merge_dict_of_lists(
    base: dict[str, Any],
    override: dict[str, Any],
) -> dict[str, Any]:
    """Merge due dict dove i valori sono liste — unione con deduplicazione."""
    result: dict[str, Any] = {}
    for key in (*base.keys(), *override.keys()):
        b = base.get(key, []) if isinstance(base.get(key), list) else []
        o = override.get(key, []) if isinstance(override.get(key), list) else []
        result[key] = _dedup(b + o)
    return result


def _dedup(items: list[str]) -> list[str]:
    """Deduplicazione con preservazione dell'ordine."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _category_to_message_type(cat: str) -> MessageType:
    mapping: dict[str, MessageType] = {
        "new_signal": "NEW_SIGNAL",
        "update": "UPDATE",
        "info_only": "INFO_ONLY",
    }
    return mapping.get(cat, "UNCLASSIFIED")
