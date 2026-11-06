"""enrich_event: how successive calls build one request's wide event."""

from types import SimpleNamespace

from app.services import polar as polar_service
from app.utilities.wide_events import enrich_event


def _request(event: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(wide_event=event or {}))


def test_successive_calls_merge_nested_context():
    request = _request()
    enrich_event(request, operation={"ai_skipped": "rate_limiter_down"})
    enrich_event(request, operation={"type": "recurring_detect", "ai_used": False})
    assert request.state.wide_event["operation"] == {
        "ai_skipped": "rate_limiter_down",
        "type": "recurring_detect",
        "ai_used": False,
    }


def test_later_values_win_and_earlier_dicts_are_not_mutated():
    request = _request()
    first = {"type": "login", "outcome": "pending"}
    enrich_event(request, auth=first, user={"id": 1})
    enrich_event(request, auth={"outcome": "success"})
    assert request.state.wide_event["auth"] == {"type": "login", "outcome": "success"}
    assert request.state.wide_event["user"] == {"id": 1}
    assert first == {"type": "login", "outcome": "pending"}


def test_merge_is_one_level_deep():
    request = _request()
    enrich_event(request, operation={"ai": {"calls": 1, "model": "m"}})
    enrich_event(request, operation={"ai": {"calls": 2}})
    assert request.state.wide_event["operation"] == {"ai": {"calls": 2}}


def test_non_dict_values_replace():
    request = _request({"http": {"path": "/x"}})
    enrich_event(request, deletion_results=3, user_id=7)
    enrich_event(request, deletion_results={"expenses": 2}, user_id=8)
    event = request.state.wide_event
    assert event["deletion_results"] == {"expenses": 2}
    assert event["user_id"] == 8
    assert event["http"] == {"path": "/x"}


def test_without_a_wide_event_is_a_no_op():
    request = SimpleNamespace(state=SimpleNamespace())
    enrich_event(request, operation={"type": "x"})
    assert not hasattr(request.state, "wide_event")


def test_each_polar_log_replaces_the_previous_record():
    """A Polar log is one complete record; fields of an earlier one must not
    leak into it."""
    request = _request()
    polar_service._polar_log(
        request, level="warning", op="customers.get", error="ResourceNotFound"
    )
    polar_service._polar_log(request, level="info", op="ignore_event")
    assert request.state.wide_event["polar"] == {"level": "info", "op": "ignore_event"}
    assert request.state.wide_event["force_log"] is True
