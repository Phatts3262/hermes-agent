"""On a platform without AF_UNIX asyncio the loop-tick witness is absent by
design -- say so once at INFO, never as a WARNING with a traceback.

Fork regression (2026-09-06). ``loop_heartbeat_forever`` armed the witness with
``asyncio.start_unix_server`` unconditionally; on native Windows that raises,
and the ``except Exception`` logged a WARNING with ``exc_info`` at every gateway
boot (four tracebacks a day in errors.log for a condition the module's own
comment calls deliberate). Upstream gates on ``os.name == "posix"`` since
2026-09-03; this pins the same gate. The fail-safe semantics are unchanged: the
heartbeat payload still carries ``loop_tick_socket: False`` so probes classify
UNKNOWN, never WEDGED.

Mutation: remove the ``os.name`` gate -> a WARNING with a traceback is logged
on Windows -> RED.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import pytest

from gateway.shutdown_watchdog import (
    get_loop_heartbeat_path,
    loop_heartbeat_forever,
)


@pytest.mark.skipif(os.name == "posix", reason="POSIX arms the AF_UNIX witness for real")
def test_windows_records_an_absent_witness_at_info_without_a_traceback(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="gateway.shutdown_watchdog")

    asyncio.run(
        loop_heartbeat_forever(interval_s=1.0, home=tmp_path, should_continue=lambda: False)
    )

    records = [r for r in caplog.records if "tick socket" in r.getMessage().lower()]
    assert records, "the platform gate must say the witness is absent"
    assert all(r.levelno == logging.INFO for r in records), [
        (r.levelname, r.getMessage()) for r in records
    ]
    assert all(r.exc_info is None for r in records), "no traceback for a designed absence"
    assert any("AF_UNIX" in r.getMessage() for r in records)

    payload = json.loads(get_loop_heartbeat_path(tmp_path).read_text(encoding="utf-8"))
    assert payload.get("loop_tick_socket") is False, payload


def test_the_gate_is_the_posix_check_upstream_uses():
    from pathlib import Path

    import gateway.shutdown_watchdog as mod

    body = Path(mod.__file__).read_text(encoding="utf-8")
    arm = body.split("async def loop_heartbeat_forever", 1)[1]
    assert 'if os.name != "posix":' in arm
    # Structural pin kept from test_loop_liveness_watchdog: the witness is
    # still awaited on the loop when it IS armed.
    assert "await asyncio.start_unix_server(" in arm
