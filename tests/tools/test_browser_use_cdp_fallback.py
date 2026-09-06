"""browser.cdp_fallback_url: browser_exec falls back to a second CDP lane when
the configured one is down (fork patch, D-05c, 2026-09-06).

The primary ``browser.cdp_url`` is the operator's logged-in HEADED Edge -- an
Interactive logon task that does not exist before anyone logs on. A configured
but unreachable override pinned every browser_exec call to the harness's 30 s
"unreachable" error. With a fallback configured (the session-0 headless lane),
the call goes there instead; with no fallback configured the upstream shape is
unchanged and nothing is probed.

Mutations: drop the ``_select_cdp_lane`` branch in ``_resolve_backend_cdp`` ->
the fallback test goes RED; probe the fallback first -> the primary-wins test
goes RED.
"""

from __future__ import annotations

import logging

import pytest

import tools.browser_use_cli as bu

PRIMARY = "http://127.0.0.1:9222"
FALLBACK = "http://127.0.0.1:9333"


@pytest.fixture
def lanes(monkeypatch):
    """Wire the resolver to a fake host: which lanes answer, what config says."""
    state = {"up": set(), "cfg": {"backend": "browser-use", "cdp_url": PRIMARY, "cdp_fallback_url": FALLBACK}, "probes": []}

    def reachable(url, timeout=1.5):
        state["probes"].append(url)
        return url in state["up"]

    monkeypatch.setattr(bu, "_cdp_reachable", reachable)
    monkeypatch.setattr(bu, "_read_browser_cfg", lambda: dict(state["cfg"]))
    bu._CDP_LANE_STATE["lane"] = None

    import tools.browser_tool as bt

    monkeypatch.setattr(bt, "_get_cdp_override_raw", lambda: state["cfg"].get("cdp_url", ""))
    monkeypatch.setattr(bt, "_resolve_cdp_override", lambda url: url)  # normalization is identity here
    monkeypatch.setattr(bt, "_get_cdp_override", lambda: state["cfg"].get("cdp_url", ""))
    monkeypatch.setattr(bt, "_get_cloud_provider", lambda: None)
    return state


def _resolve(env=None):
    env = {} if env is None else env
    err = bu._resolve_backend_cdp(env, task_id="t1")
    return err, env


def test_primary_wins_when_it_answers(lanes):
    lanes["up"] = {PRIMARY, FALLBACK}
    err, env = _resolve()
    assert err is None
    assert env["BU_CDP_URL"] == PRIMARY
    assert lanes["probes"] == [PRIMARY], "the fallback is not even probed while the primary answers"


def test_falls_back_when_the_primary_is_down(lanes, caplog):
    lanes["up"] = {FALLBACK}
    caplog.set_level(logging.INFO, logger="tools.browser_use_cli")
    err, env = _resolve()
    assert err is None
    assert env["BU_CDP_URL"] == FALLBACK
    assert any("fallback" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)


def test_both_down_keeps_the_primary_so_the_harness_reports_it(lanes):
    lanes["up"] = set()
    err, env = _resolve()
    assert err is None
    assert env["BU_CDP_URL"] == PRIMARY


def test_no_fallback_configured_means_no_probe_and_upstream_shape(lanes):
    lanes["cfg"].pop("cdp_fallback_url")
    lanes["up"] = set()
    err, env = _resolve()
    assert err is None
    assert env["BU_CDP_URL"] == PRIMARY
    assert lanes["probes"] == []


def test_lane_changes_are_logged_once_not_per_call(lanes, caplog):
    lanes["up"] = {FALLBACK}
    caplog.set_level(logging.INFO, logger="tools.browser_use_cli")
    _resolve(); _resolve(); _resolve()
    switches = [r for r in caplog.records if "CDP lane" in r.getMessage()]
    assert len(switches) == 1
    lanes["up"] = {PRIMARY, FALLBACK}
    _resolve()
    switches = [r for r in caplog.records if "CDP lane" in r.getMessage()]
    assert len(switches) == 2 and "primary" in switches[-1].getMessage()


def test_a_ws_fallback_is_handed_to_the_harness_as_ws(lanes):
    lanes["cfg"]["cdp_fallback_url"] = "ws://127.0.0.1:9333/devtools/browser/abc"
    lanes["up"] = {"ws://127.0.0.1:9333/devtools/browser/abc"}
    err, env = _resolve()
    assert err is None
    assert env["BU_CDP_WS"] == "ws://127.0.0.1:9333/devtools/browser/abc"
    assert "BU_CDP_URL" not in env


def test_an_explicit_bu_env_override_is_untouched(lanes):
    lanes["up"] = {FALLBACK}
    err, env = _resolve({"BU_CDP_URL": "http://10.0.0.5:9222"})
    assert err is None
    assert env["BU_CDP_URL"] == "http://10.0.0.5:9222"
    assert lanes["probes"] == []
