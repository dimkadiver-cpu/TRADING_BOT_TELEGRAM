"""Validatore di configurazione all'avvio.

Verifica, prima che il runtime parta:
  - variabili d'ambiente richieste/opzionali
  - presenza e struttura delle directory attese
  - esistenza e parsabilità dei file di configurazione
  - coerenza incrociata (trader registrati ↔ file traders/, canali ↔ profili parser,
    adapter execution ↔ credenziali env, placeholder ${ENV} del control plane)

Non modifica nulla: produce solo un report. Usato da main.py all'avvio e
invocabile standalone con `python -m src.startup_check`.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml

_ENV_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class Severity(str, Enum):
    OK = "OK"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass(slots=True)
class CheckResult:
    section: str
    severity: Severity
    message: str


@dataclass(slots=True)
class ValidationReport:
    results: list[CheckResult] = field(default_factory=list)

    def add(self, section: str, severity: Severity, message: str) -> None:
        self.results.append(CheckResult(section, severity, message))

    def ok(self, section: str, message: str) -> None:
        self.add(section, Severity.OK, message)

    def warn(self, section: str, message: str) -> None:
        self.add(section, Severity.WARNING, message)

    def error(self, section: str, message: str) -> None:
        self.add(section, Severity.ERROR, message)

    @property
    def errors(self) -> list[CheckResult]:
        return [r for r in self.results if r.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[CheckResult]:
        return [r for r in self.results if r.severity is Severity.WARNING]

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    def render(self) -> str:
        icons = {Severity.OK: "✅", Severity.WARNING: "⚠️ ", Severity.ERROR: "❌"}
        lines = ["", "=== Verifica configurazione all'avvio ==="]
        current_section: str | None = None
        for result in self.results:
            if result.section != current_section:
                current_section = result.section
                lines.append(f"\n[{current_section}]")
            lines.append(f"  {icons[result.severity]} {result.message}")
        lines.append("")
        n_err, n_warn = len(self.errors), len(self.warnings)
        if n_err:
            lines.append(f"RISULTATO: {n_err} errori, {n_warn} warning — configurazione NON valida")
        elif n_warn:
            lines.append(f"RISULTATO: 0 errori, {n_warn} warning — configurazione valida con avvisi")
        else:
            lines.append("RISULTATO: configurazione valida")
        lines.append("")
        return "\n".join(lines)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _load_yaml(path: Path) -> dict | None:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else None


def _check_file_parses(report: ValidationReport, section: str, path: Path) -> dict | None:
    """Verifica esistenza e parsabilità di un file YAML/JSON. Ritorna il contenuto o None."""
    rel = path.name
    if not path.exists():
        report.error(section, f"file mancante: {path}")
        return None
    try:
        if path.suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            data = _load_yaml(path)
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        report.error(section, f"{rel}: parsing fallito — {exc}")
        return None
    if data is None:
        report.error(section, f"{rel}: vuoto o struttura top-level non valida (atteso mapping)")
        return None
    report.ok(section, f"{rel}: presente e parsabile")
    return data


# ── Sezioni di verifica ──────────────────────────────────────────────────────


def _check_env(report: ValidationReport, root_dir: Path) -> None:
    section = "Variabili d'ambiente"

    for name in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH"):
        value = os.getenv(name)
        if not value:
            report.error(section, f"{name} non impostata (obbligatoria per il listener Telegram)")
        elif name == "TELEGRAM_API_ID" and not value.isdigit():
            report.error(section, f"{name} deve essere un intero, trovato: {value!r}")
        else:
            report.ok(section, f"{name} impostata")

    log_level = os.getenv("LOG_LEVEL")
    if log_level and log_level.upper() not in _VALID_LOG_LEVELS:
        report.warn(
            section,
            f"LOG_LEVEL={log_level!r} non riconosciuto (attesi: {', '.join(sorted(_VALID_LOG_LEVELS))})",
        )

    for name, default in (
        ("PARSER_DB_PATH", root_dir / "db" / "parser.sqlite3"),
        ("OPS_DB_PATH", root_dir / "db" / "ops.sqlite3"),
        ("LOG_PATH", root_dir / "logs" / "bot.log"),
    ):
        override = os.getenv(name)
        path = Path(override) if override else Path(default)
        if override and not path.parent.exists():
            report.warn(section, f"{name}={override}: la directory padre {path.parent} non esiste")
        elif override:
            report.ok(section, f"{name} override valido: {override}")


def _check_directories(report: ValidationReport, root_dir: Path) -> None:
    section = "Directory"

    required = [
        ("config/", root_dir / "config", None),
        ("config/traders/", root_dir / "config" / "traders", None),
        ("db/migrations/", root_dir / "db" / "migrations", "*.sql"),
        ("db/ops_migrations/", root_dir / "db" / "ops_migrations", "*.sql"),
        ("src/parser_v2/profiles/", root_dir / "src" / "parser_v2" / "profiles", None),
    ]
    for label, path, expected_glob in required:
        if not path.is_dir():
            report.error(section, f"directory mancante: {label} ({path})")
            continue
        if expected_glob and not list(path.glob(expected_glob)):
            report.error(section, f"{label}: nessun file {expected_glob} trovato")
        else:
            report.ok(section, f"{label} presente")

    # logs/ e db/ vengono create automaticamente: segnala solo se il padre non è scrivibile
    for label, path in (("logs/", root_dir / "logs"), ("db/", root_dir / "db")):
        if not path.exists() and not os.access(path.parent, os.W_OK):
            report.error(section, f"{label} non esiste e {path.parent} non è scrivibile")


def _registered_traders(root_dir: Path) -> list[str]:
    try:
        raw = _load_yaml(root_dir / "config" / "operation_config.yaml") or {}
    except Exception:
        return []
    traders = raw.get("registered_traders")
    return [str(t) for t in traders] if isinstance(traders, list) else []


def _check_channels(report: ValidationReport, root_dir: Path) -> None:
    section = "channels.yaml"
    path = root_dir / "config" / "channels.yaml"
    raw = _check_file_parses(report, section, path)
    if raw is None:
        return

    # Validazione strutturale tramite il loader reale (duplicati, topic_id, tipi)
    try:
        from src.telegram.channel_config import load_channels_config

        load_channels_config(str(path))
        report.ok(section, "struttura canali valida (loader runtime)")
    except Exception as exc:
        report.error(section, f"struttura canali non valida: {exc}")
        return

    try:
        from src.parser_v2.profiles.registry import canonicalize_trader_v2
    except Exception as exc:
        report.error(section, f"registry profili parser non importabile: {exc}")
        canonicalize_trader_v2 = None  # type: ignore[assignment]

    registered = set(_registered_traders(root_dir))

    for entry in raw.get("channels") or []:
        if not isinstance(entry, dict):
            continue
        label = entry.get("label") or entry.get("chat_id")
        active = bool(entry.get("active", True))
        emit = report.error if active else report.warn
        suffix = "" if active else " (canale non attivo)"

        trader_id = entry.get("trader_id")
        if trader_id is not None and registered and str(trader_id) not in registered:
            emit(
                section,
                f"canale '{label}': trader_id '{trader_id}' non presente in "
                f"operation_config.yaml:registered_traders{suffix}",
            )

        profile = entry.get("parser_profile") or trader_id
        if profile is not None and canonicalize_trader_v2 is not None:
            canonical = canonicalize_trader_v2(str(profile))
            if canonical is None:
                emit(
                    section,
                    f"canale '{label}': parser_profile '{profile}' sconosciuto al registry parser_v2{suffix}",
                )
            else:
                _check_profile_files(report, root_dir, canonical, label=str(label), active=active)


def _check_profile_files(
    report: ValidationReport, root_dir: Path, profile: str, *, label: str, active: bool
) -> None:
    section = "Profili parser"
    profile_dir = root_dir / "src" / "parser_v2" / "profiles" / profile
    emit = report.error if active else report.warn
    if not profile_dir.is_dir():
        emit(section, f"profilo '{profile}' (canale '{label}'): directory mancante {profile_dir}")
        return
    for filename in ("rules.json", "semantic_markers.json"):
        file_path = profile_dir / filename
        if not file_path.exists():
            emit(section, f"profilo '{profile}': file mancante {filename}")
            continue
        try:
            json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            emit(section, f"profilo '{profile}': {filename} non è JSON valido — {exc}")


def _check_operation_config(report: ValidationReport, root_dir: Path) -> None:
    section = "operation_config.yaml"
    path = root_dir / "config" / "operation_config.yaml"
    raw = _check_file_parses(report, section, path)
    if raw is None:
        return

    registered = raw.get("registered_traders")
    if not isinstance(registered, list) or not registered:
        report.error(section, "registered_traders mancante o vuoto")
        return

    try:
        from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader

        loader = OperationConfigLoader(str(root_dir / "config"))
    except Exception as exc:
        report.error(section, f"caricamento config operativa fallito: {exc}")
        return

    for trader_id in registered:
        trader_id = str(trader_id)
        override_path = root_dir / "config" / "traders" / f"{trader_id}.yaml"
        if not override_path.exists():
            report.warn(
                section,
                f"trader '{trader_id}' registrato ma senza override config/traders/{trader_id}.yaml "
                "(userà solo i defaults globali)",
            )
        try:
            effective = loader.get_effective_config(trader_id)
            if effective is None:
                report.error(section, f"trader '{trader_id}': config effettiva non risolvibile")
            else:
                report.ok(section, f"trader '{trader_id}': config effettiva valida")
        except Exception as exc:
            report.error(section, f"trader '{trader_id}': config non valida — {exc}")

    per_trader = (raw.get("symbol_blacklist") or {}).get("per_trader") or {}
    for trader_id in per_trader:
        if str(trader_id) not in {str(t) for t in registered}:
            report.warn(
                section,
                f"symbol_blacklist.per_trader contiene '{trader_id}' che non è tra i registered_traders",
            )


def _check_execution_config(report: ValidationReport, root_dir: Path) -> None:
    section = "execution.yaml"
    path = root_dir / "config" / "execution.yaml"
    raw = _check_file_parses(report, section, path)
    if raw is None:
        return

    execution = raw.get("execution")
    if not isinstance(execution, dict):
        report.error(section, "chiave top-level 'execution' mancante")
        return

    # Validazione schema tramite il loader/modello Pydantic reale
    try:
        from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader

        ExecutionConfigLoader(str(path)).load()
        report.ok(section, "schema execution valido (modello runtime)")
    except Exception as exc:
        report.error(section, f"schema execution non valido: {exc}")

    adapters = execution.get("adapters") or {}
    default_adapter = execution.get("default_adapter")
    if default_adapter not in adapters:
        report.error(
            section,
            f"default_adapter '{default_adapter}' non definito in adapters "
            f"(disponibili: {', '.join(adapters) or 'nessuno'})",
        )

    # Adapter effettivamente instradati: default + account_routing
    routed = {default_adapter} if default_adapter else set()
    for route in (execution.get("account_routing") or {}).values():
        if isinstance(route, dict) and route.get("adapter"):
            routed.add(route["adapter"])
    for name, route in (execution.get("account_routing") or {}).items():
        if isinstance(route, dict) and route.get("adapter") not in adapters:
            report.error(section, f"account_routing.{name}: adapter '{route.get('adapter')}' inesistente")

    for adapter_name in sorted(a for a in routed if a in adapters):
        cfg = adapters[adapter_name] or {}
        mode = cfg.get("mode")
        for key in ("api_key_env", "api_secret_env"):
            env_name = cfg.get(key)
            if not env_name:
                if mode != "paper":
                    report.warn(section, f"adapter '{adapter_name}': {key} non dichiarato (mode={mode})")
                continue
            if not os.getenv(env_name):
                report.error(
                    section,
                    f"adapter '{adapter_name}': variabile {env_name} ({key}) non impostata nell'ambiente",
                )
            else:
                report.ok(section, f"adapter '{adapter_name}': {env_name} impostata")

        if mode == "live":
            local_gate = (cfg.get("live_safety") or {}).get("allow_live_trading")
            if not local_gate:
                report.error(
                    section,
                    f"adapter '{adapter_name}': mode=live ma live_safety.allow_live_trading=false",
                )
            if os.getenv("TSB_ALLOW_LIVE_TRADING") != "YES_I_UNDERSTAND":
                report.error(
                    section,
                    f"adapter '{adapter_name}': mode=live richiede env TSB_ALLOW_LIVE_TRADING=YES_I_UNDERSTAND",
                )


def _check_account_routing(report: ValidationReport, root_dir: Path) -> None:
    """Coerenza account: operation_config/traders ↔ execution.account_routing.

    Copre la checklist di config/ISTRUZIONI_ACCOUNT_EXCHANGE.md:
    account.id nel trader yaml deve corrispondere a una chiave di account_routing.
    """
    section = "Routing account"
    try:
        op_raw = _load_yaml(root_dir / "config" / "operation_config.yaml") or {}
        exec_raw = _load_yaml(root_dir / "config" / "execution.yaml") or {}
    except Exception:
        return  # parsing già segnalato nelle sezioni dedicate

    routing = (exec_raw.get("execution") or {}).get("account_routing") or {}
    if "default" not in routing:
        report.error(section, "execution.yaml: account_routing.default mancante (richiesto dal runtime)")
    if not routing:
        return

    account_mode = op_raw.get("account_mode", "single")
    global_account_id = (op_raw.get("account") or {}).get("id", "main")
    if account_mode == "per_trader_subaccount" and global_account_id not in routing:
        report.warn(
            section,
            f"account globale '{global_account_id}' senza routing dedicato in account_routing "
            "(i trader senza override useranno il routing 'default')",
        )

    for trader_path in sorted((root_dir / "config" / "traders").glob("*.yaml")):
        try:
            trader_raw = _load_yaml(trader_path) or {}
        except Exception as exc:
            report.error(section, f"{trader_path.name}: parsing fallito — {exc}")
            continue
        account = trader_raw.get("account")
        if not isinstance(account, dict):
            continue
        account_id = account.get("id")
        if account_mode == "single":
            report.warn(
                section,
                f"{trader_path.name}: blocco 'account' definito ma account_mode=single — "
                "verrà ignorato (serve per_trader_subaccount)",
            )
        elif account_id and account_id not in routing:
            report.error(
                section,
                f"{trader_path.name}: account.id '{account_id}' non ha una chiave corrispondente "
                f"in execution.yaml:account_routing (disponibili: {', '.join(routing)})",
            )
        elif account_id:
            report.ok(section, f"{trader_path.name}: account.id '{account_id}' → routing presente")

    # Trader risolti dinamicamente via alias nei canali multi-trader
    registered = set(_registered_traders(root_dir))
    if registered:
        try:
            channels_raw = _load_yaml(root_dir / "config" / "channels.yaml") or {}
        except Exception:
            return
        for entry in channels_raw.get("channels") or []:
            if not isinstance(entry, dict):
                continue
            aliases = (entry.get("resolution") or {}).get("aliases") or {}
            for tag, resolved in aliases.items():
                if resolved and str(resolved) not in registered:
                    emit = report.error if entry.get("active") else report.warn
                    emit(
                        section,
                        f"canale '{entry.get('label')}': alias {tag} → '{resolved}' non presente "
                        "in registered_traders"
                        + ("" if entry.get("active") else " (canale non attivo)"),
                    )


def _check_control_plane(report: ValidationReport, root_dir: Path) -> None:
    section = "telegram_control.yaml"
    path = root_dir / "config" / "telegram_control.yaml"
    raw = _check_file_parses(report, section, path)
    if raw is None:
        return

    if not raw.get("enabled", False):
        report.ok(section, "control plane disabilitato — verifiche credenziali saltate")
        return

    env_complete = True

    token_env = raw.get("token_env")
    if not raw.get("token"):
        if not token_env:
            report.error(section, "manca 'token' o 'token_env'")
            env_complete = False
        elif not os.getenv(str(token_env)):
            report.error(section, f"variabile {token_env} (token_env) non impostata")
            env_complete = False
        else:
            report.ok(section, f"token bot disponibile via {token_env}")

    # Tutti i placeholder ${ENV} referenziati nei valori (non nei commenti) devono esistere
    def _collect_placeholders(value: object) -> set[str]:
        if isinstance(value, str):
            return set(_ENV_PLACEHOLDER.findall(value))
        if isinstance(value, dict):
            return set().union(*(_collect_placeholders(v) for v in value.values()), set())
        if isinstance(value, list):
            return set().union(*(_collect_placeholders(v) for v in value), set())
        return set()

    for env_name in sorted(_collect_placeholders(raw)):
        if not os.getenv(env_name):
            report.error(section, f"placeholder ${{{env_name}}} referenziato ma variabile non impostata")
            env_complete = False
        else:
            report.ok(section, f"placeholder ${{{env_name}}} risolvibile")

    if env_complete:
        try:
            from src.runtime_v2.control_plane.config import load_control_plane_config

            load_control_plane_config(str(path))
            report.ok(section, "config control plane valida (modello runtime)")
        except Exception as exc:
            report.error(section, f"config control plane non valida: {exc}")

    # Coerenza per_trader ↔ trader_id dichiarati nei canali
    per_trader = (
        ((raw.get("topics") or {}).get("clean_log") or {}).get("per_trader") or {}
    )
    if per_trader:
        try:
            channels_raw = _load_yaml(root_dir / "config" / "channels.yaml") or {}
            known: set[str] = set()
            for entry in channels_raw.get("channels") or []:
                if isinstance(entry, dict):
                    if entry.get("trader_id"):
                        known.add(str(entry["trader_id"]))
                    aliases = (entry.get("resolution") or {}).get("aliases") or {}
                    known.update(str(v) for v in aliases.values())
            for trader_id in per_trader:
                if str(trader_id) not in known:
                    report.warn(
                        section,
                        f"clean_log.per_trader: '{trader_id}' non corrisponde a nessun trader in channels.yaml",
                    )
        except Exception:
            pass


def _check_misc_files(report: ValidationReport, root_dir: Path) -> None:
    section = "Altri file"
    aliases_path = root_dir / "config" / "trader_aliases.json"
    if aliases_path.exists():
        _check_file_parses(report, section, aliases_path)
    else:
        report.warn(section, f"file opzionale assente: {aliases_path.relative_to(root_dir)}")

    # .env non deve finire in git
    gitignore = root_dir / ".gitignore"
    if (root_dir / ".env").exists():
        ignored = gitignore.exists() and any(
            line.strip() in {".env", "/.env", ".env*"}
            for line in gitignore.read_text(encoding="utf-8").splitlines()
        )
        if not ignored:
            report.error(section, ".env presente ma non elencato in .gitignore — rischio commit di segreti")
        else:
            report.ok(section, ".env presente e ignorato da git")


# ── Entry point ──────────────────────────────────────────────────────────────


def run_startup_checks(root_dir: Path) -> ValidationReport:
    """Esegue tutte le verifiche di configurazione e ritorna il report."""
    report = ValidationReport()
    _check_env(report, root_dir)
    _check_directories(report, root_dir)
    _check_channels(report, root_dir)
    _check_operation_config(report, root_dir)
    _check_execution_config(report, root_dir)
    _check_account_routing(report, root_dir)
    _check_control_plane(report, root_dir)
    _check_misc_files(report, root_dir)
    return report


__all__ = ["CheckResult", "Severity", "ValidationReport", "run_startup_checks"]
