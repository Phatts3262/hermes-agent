"""A check_fn that returns False BY DESIGN must not page the log every turn.

Fork regression (2026-09-06). In Browser Use mode the eleven built-in browser
tools are superseded by ``browser_exec`` on purpose, so their check_fns return
False on every turn -- and ``tools.registry._check_fn_cached`` logged eleven
WARNINGs per turn for a condition the operator chose (176 lines a day, live).
A check_fn may now carry a ``quiet_unavailable`` callable; when it returns
True the designed loss is logged at DEBUG. A check_fn that RAISED is never
quiet, and one without the marker keeps the WARNING.

Mutations: drop the ``designed`` branch in the registry -> the quiet test goes
RED; drop the marker loop at the bottom of browser_tool.py -> the marker test
goes RED.
"""

from __future__ import annotations

import logging

import pytest

import tools.registry as registry


def _fresh_false_check(name: str):
    def fn():
        return False

    fn.__qualname__ = name
    return fn


def _run_uncached(fn):
    # A fresh function object is a fresh cache key; bypass the memo anyway.
    registry._check_fn_cache.pop((fn, registry.check_fn_cache_scope()), None)
    return registry._check_fn_cached(fn)


def test_a_designed_false_is_logged_at_debug_not_warning(caplog):
    fn = _fresh_false_check("designed_off")
    fn.quiet_unavailable = lambda: True
    caplog.set_level(logging.DEBUG, logger="tools.registry")

    assert _run_uncached(fn) is False

    records = [r for r in caplog.records if "designed_off" in r.getMessage()]
    assert records and all(r.levelno == logging.DEBUG for r in records), [
        (r.levelname, r.getMessage()) for r in records
    ]
    assert any("superseded by design" in r.getMessage() for r in records)


def test_an_undeclared_false_still_warns(caplog):
    fn = _fresh_false_check("plain_off")
    caplog.set_level(logging.DEBUG, logger="tools.registry")

    assert _run_uncached(fn) is False

    records = [r for r in caplog.records if "plain_off" in r.getMessage()]
    assert records and all(r.levelno == logging.WARNING for r in records)


def test_a_marker_that_says_not_now_still_warns(caplog):
    fn = _fresh_false_check("marker_false")
    fn.quiet_unavailable = lambda: False
    caplog.set_level(logging.DEBUG, logger="tools.registry")

    assert _run_uncached(fn) is False
    records = [r for r in caplog.records if "marker_false" in r.getMessage()]
    assert records and all(r.levelno == logging.WARNING for r in records)


def test_a_raising_check_fn_is_never_quiet(caplog):
    def fn():
        raise RuntimeError("probe blew up")

    fn.__qualname__ = "raiser"
    fn.quiet_unavailable = lambda: True
    caplog.set_level(logging.DEBUG, logger="tools.registry")

    assert _run_uncached(fn) is False
    records = [r for r in caplog.records if "raiser" in r.getMessage()]
    assert records and all(r.levelno == logging.WARNING for r in records)
    assert any("raised" in r.getMessage() for r in records)


def test_a_broken_marker_falls_back_to_the_warning(caplog):
    fn = _fresh_false_check("broken_marker")

    def boom():
        raise ValueError("marker broke")

    fn.quiet_unavailable = boom
    caplog.set_level(logging.DEBUG, logger="tools.registry")

    assert _run_uncached(fn) is False
    records = [r for r in caplog.records if "broken_marker" in r.getMessage()]
    assert records and all(r.levelno == logging.WARNING for r in records)


BROWSER_CHECKS = (
    "check_browser_requirements",
    "check_browser_vision_requirements",
    "check_browser_navigate_requirements",
    "check_browser_snapshot_requirements",
    "check_browser_click_requirements",
    "check_browser_type_requirements",
    "check_browser_scroll_requirements",
    "check_browser_back_requirements",
    "check_browser_press_requirements",
)


@pytest.mark.parametrize("name", BROWSER_CHECKS)
def test_every_built_in_browser_check_declares_browser_use_supersession(name):
    import tools.browser_tool as bt

    fn = getattr(bt, name)
    assert getattr(fn, "quiet_unavailable", None) is bt._superseded_by_browser_use


def test_the_cdp_and_dialog_checks_follow_the_same_switch(monkeypatch):
    import tools.browser_cdp_tool as cdp
    import tools.browser_dialog_tool as dlg
    import tools.browser_tool as bt

    monkeypatch.setattr(bt, "_is_browser_use_cli_mode", lambda: True)
    assert cdp._browser_cdp_check.quiet_unavailable() is True
    assert dlg._browser_dialog_check.quiet_unavailable() is True
    monkeypatch.setattr(bt, "_is_browser_use_cli_mode", lambda: False)
    assert cdp._browser_cdp_check.quiet_unavailable() is False
    assert dlg._browser_dialog_check.quiet_unavailable() is False


def test_browser_use_mode_makes_the_built_in_loss_quiet_end_to_end(monkeypatch, caplog):
    """The live shape: Browser Use mode on -> check_browser_requirements False
    -> DEBUG, not WARNING. Off -> whatever the host has, logged honestly."""
    import tools.browser_tool as bt

    monkeypatch.setattr(bt, "_is_browser_use_cli_mode", lambda: True)
    caplog.set_level(logging.DEBUG, logger="tools.registry")

    assert _run_uncached(bt.check_browser_requirements) is False
    records = [r for r in caplog.records if "check_browser_requirements" in r.getMessage()]
    assert records and all(r.levelno == logging.DEBUG for r in records), [
        (r.levelname, r.getMessage()) for r in records
    ]
