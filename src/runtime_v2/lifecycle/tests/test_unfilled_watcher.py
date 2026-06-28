"""Tests for Task 1: verify plumbing — event type, outbox map, template."""
from src.runtime_v2.lifecycle.models import LifecycleEventType
from src.runtime_v2.control_plane.outbox_writer import _CLEAN_LOG_EVENT_MAP
from src.runtime_v2.control_plane.formatters.templates.clean_log import TEMPLATE_REGISTRY


def test_unfilled_tp_cancel_in_lifecycle_event_type():
    # LifecycleEventType is a Literal — check its args
    import typing
    args = typing.get_args(LifecycleEventType)
    assert "UNFILLED_TP_CANCEL" in args


def test_outbox_map_has_entry_cancelled_tp_reached():
    assert _CLEAN_LOG_EVENT_MAP.get("UNFILLED_TP_CANCEL") == "ENTRY_CANCELLED_TP_REACHED"


def test_template_map_has_entry_cancelled_tp_reached():
    assert "ENTRY_CANCELLED_TP_REACHED" in TEMPLATE_REGISTRY
