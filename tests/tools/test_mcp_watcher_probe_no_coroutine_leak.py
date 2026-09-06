"""The stdio-children watcher probe must not create a coroutine it never awaits.

Fork regression (2026-09-06). ``_make_tool_handler`` decides whether to race the
RPC against ``server._watch_stdio_children`` by probing the watcher. The probe
used ``inspect.isawaitable(_watch_children())`` -- it CALLED the coroutine
function, tested the coroutine object, and dropped it, so every stdio tool call
logged ``RuntimeWarning: coroutine 'MCPServerTask._watch_stdio_children' was
never awaited`` (live gateway-stdio.log). Upstream's handlers module probes the
function instead (``inspect.iscoroutinefunction``); this pins the same shape.

Mutation: restore ``inspect.isawaitable(_watch_children())`` -> the recorded
warnings contain "never awaited" -> RED.
"""

from __future__ import annotations

import asyncio
import gc
import json
import warnings
from unittest.mock import MagicMock

import pytest

pytest.importorskip("mcp")

from tests.tools.test_mcp_stdio_fastfail_reconnect import (  # noqa: E402
    _cleanup,
    _install_stub_server,
)


def test_a_healthy_rpc_with_a_live_watcher_leaks_no_coroutine(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from tools import mcp_tool
    from tools.mcp_tool import _make_tool_handler

    async def _ok_call(*a, **kw):
        return MagicMock(is_error=False, content=[])

    server = _install_stub_server(
        mcp_tool, "srv-probe", _ok_call, children_dead=lambda: False
    )
    watcher_entered = 0

    async def _watch_children():
        nonlocal watcher_entered
        watcher_entered += 1
        await asyncio.sleep(30)  # children stay alive; the RPC wins the race

    server._watch_stdio_children = _watch_children
    mcp_tool._ensure_mcp_loop()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            handler = _make_tool_handler("srv-probe", "tool1", 10.0)
            result = handler({})
        finally:
            _cleanup(mcp_tool, "srv-probe")
        gc.collect()

    parsed = json.loads(result)
    assert "error" not in parsed, parsed
    leaked = [str(w.message) for w in caught if "never awaited" in str(w.message)]
    assert not leaked, leaked
    # The race itself must still be armed: the watcher ran exactly once
    # (as the raced task), not twice (probe + task) and not zero times.
    assert watcher_entered == 1


def test_the_probe_inspects_the_function_not_a_call_to_it():
    from pathlib import Path

    import tools.mcp_tool as mod

    body = Path(mod.__file__).read_text(encoding="utf-8")
    assert "inspect.isawaitable(_watch_children())" not in body
    assert "inspect.iscoroutinefunction(_watch_children)" in body
