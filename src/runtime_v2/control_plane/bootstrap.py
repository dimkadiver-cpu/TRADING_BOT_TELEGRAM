from __future__ import annotations

from dataclasses import dataclass

from src.runtime_v2.control_plane.audit_store import CommandAuditStore
from src.runtime_v2.control_plane.auth import AuthValidator
from src.runtime_v2.control_plane.config import (
    ControlPlaneConfigError,
    load_control_plane_config,
)
from src.runtime_v2.control_plane.dashboard_manager import DashboardManager
from src.runtime_v2.control_plane.debug_controller import DebugModeController
from src.runtime_v2.control_plane.models import ControlPlaneConfig
from src.runtime_v2.control_plane.notification_dispatcher import (
    TelegramBotSender,
    TelegramNotificationDispatcher,
    build_telegram_request,
)
from src.runtime_v2.control_plane.scope_resolver import ScopeResolver
from src.runtime_v2.control_plane.service import RuntimeControlService
from src.runtime_v2.control_plane.snapshot_store import SnapshotStore
from src.runtime_v2.control_plane.startup import StartupPlan, resolve_startup
from src.runtime_v2.control_plane.telegram_bot import CommandRouter, TelegramControlBot
from src.runtime_v2.control_plane.topic_router import TopicRouter


def _create_sender(token: str):
    from telegram import Bot

    return TelegramBotSender(Bot(token=token, request=build_telegram_request()))


@dataclass(frozen=True)
class ControlPlane:
    config: ControlPlaneConfig
    service: RuntimeControlService
    bot: TelegramControlBot
    dispatcher: TelegramNotificationDispatcher
    snapshot_store: SnapshotStore
    startup_plan: StartupPlan


def build_control_plane(
    *,
    config_path: str,
    ops_db_path: str,
    log_path: str | None,
    known_trader_ids: set[str] | None = None,
) -> ControlPlane | None:
    try:
        config = load_control_plane_config(config_path)
    except ControlPlaneConfigError:
        return None

    if not config.enabled:
        return None

    default_acc = config.get_account(None)
    debug_controller = DebugModeController(
        max_seconds=default_acc.topics.tech_log.debug_max_duration_minutes * 60
    )
    service = RuntimeControlService(
        ops_db_path=ops_db_path,
        log_path=log_path,
        debug_controller=debug_controller,
    )
    auth = AuthValidator(config)
    audit = CommandAuditStore(ops_db_path)
    router = CommandRouter(config=config, auth=auth, audit=audit, service=service)
    scope_resolver = ScopeResolver(config)

    # DashboardManager created before bot — bot wired via set_bot() after creation
    dashboard_manager = DashboardManager(
        ops_db_path=ops_db_path,
        scope_resolver=scope_resolver,
        queries=service._queries,
        bot=None,  # wired below after bot creation
    )

    bot = TelegramControlBot(
        config=config,
        router=router,
        dashboard_manager=dashboard_manager,
        scope_resolver=scope_resolver,
    )
    topic_router = TopicRouter(config, known_trader_ids=known_trader_ids)
    dispatcher = TelegramNotificationDispatcher(
        config=config,
        ops_db_path=ops_db_path,
        topic_router=topic_router,
        sender=_create_sender(config.token),
        debug_status=service.debug_status,
        on_clean_log_sent=dashboard_manager.on_trade_event,
    )
    dispatcher.reset_stale_sending()

    snapshot_store = SnapshotStore(ops_db_path)
    startup_plan = resolve_startup(
        mode=config.startup.mode,
        restore_max_age_seconds=config.startup.restore_max_age_seconds,
        latest_snapshot=snapshot_store.get_latest(),
    )

    return ControlPlane(
        config=config,
        service=service,
        bot=bot,
        dispatcher=dispatcher,
        snapshot_store=snapshot_store,
        startup_plan=startup_plan,
    )


__all__ = ["ControlPlane", "build_control_plane"]
