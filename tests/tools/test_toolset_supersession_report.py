"""A toolset replaced on purpose is information in doctor, not a warning.

Fork regression (2026-09-06). ``hermes doctor`` printed
``browser (system dependency not met)`` / ``browser-cdp (system dependency not
met)`` as warnings while the managed browser worked -- Browser Use mode replaces
the built-in browser tools with ``browser_exec`` by design. The registry now
reports ``superseded`` for a toolset whose EVERY check_fn carries a truthy
``quiet_unavailable`` marker, and doctor renders that as an info line.

Mutations: drop the ``superseded`` key in ``check_tool_availability`` -> the
registry test goes RED; drop the ``superseded`` branch in
``_report_unavailable_toolset`` -> the doctor test goes RED.
"""

from __future__ import annotations

import pytest

import tools.registry as registry_mod

SCHEMA = {"name": "t", "description": "", "parameters": {"type": "object", "properties": {}}}


def _reg_with(toolset: str, *check_fns):
    reg = registry_mod.ToolRegistry()
    for i, fn in enumerate(check_fns):
        reg.register(
            name=f"{toolset}-tool-{i}",
            toolset=toolset,
            schema=dict(SCHEMA, name=f"{toolset}-tool-{i}"),
            handler=lambda args, **kw: "{}",
            check_fn=fn,
        )
    return reg


def _quiet_false():
    return False


_quiet_false.quiet_unavailable = lambda: True


def _plain_false():
    return False


def _marker_off_false():
    return False


_marker_off_false.quiet_unavailable = lambda: False


def _item(reg, toolset):
    _avail, unavailable = reg.check_tool_availability()
    matches = [i for i in unavailable if i.get("name") == toolset]
    assert len(matches) == 1, unavailable
    return matches[0]


def test_a_toolset_whose_every_check_is_designed_off_is_superseded():
    item = _item(_reg_with("fake-superseded", _quiet_false, _quiet_false), "fake-superseded")
    assert item["superseded"] is True


@pytest.mark.parametrize(
    "fns",
    [
        (_plain_false,),
        (_quiet_false, _plain_false),
        (_quiet_false, _marker_off_false),
    ],
    ids=["undeclared", "mixed-with-undeclared", "mixed-with-marker-off"],
)
def test_one_undeclared_tool_makes_it_a_real_loss(fns):
    item = _item(_reg_with("fake-lost", *fns), "fake-lost")
    assert item["superseded"] is False


def test_the_helper_treats_a_broken_marker_as_not_designed():
    def boom():
        raise ValueError("marker broke")

    def fn():
        return False

    fn.quiet_unavailable = boom
    item = _item(_reg_with("fake-broken", fn), "fake-broken")
    assert item["superseded"] is False


def test_doctor_renders_a_superseded_toolset_as_information(monkeypatch):
    from hermes_cli import doctor

    infos, warns = [], []
    monkeypatch.setattr(doctor, "check_info", lambda text: infos.append(text))
    monkeypatch.setattr(doctor, "check_warn", lambda text, detail="": warns.append((text, detail)))

    doctor._report_unavailable_toolset({"name": "browser", "env_vars": [], "superseded": True})
    doctor._report_unavailable_toolset({"name": "browser-cdp", "env_vars": [], "superseded": True})
    doctor._report_unavailable_toolset({"name": "docker", "env_vars": [], "superseded": False})
    doctor._report_unavailable_toolset({"name": "honcho", "env_vars": ["HONCHO_API_KEY"]})

    assert [t.split(" ", 1)[0] for t in infos] == ["browser", "browser-cdp"]
    assert all("superseded by Browser Use mode" in t for t in infos)
    assert warns == [
        ("docker", "(system dependency not met)"),
        ("honcho", "(missing HONCHO_API_KEY)"),
    ]


def test_the_live_browser_toolsets_are_superseded_when_browser_use_mode_is_on(monkeypatch):
    """The live shape on this host: Browser Use mode on -> the built-in
    ``browser`` and ``browser-cdp`` toolsets are reported superseded."""
    import tools.browser_cdp_tool  # noqa: F401 -- registers the browser-cdp toolset
    import tools.browser_dialog_tool  # noqa: F401 -- registers browser_dialog
    import tools.browser_tool as bt

    monkeypatch.setattr(bt, "_is_browser_use_cli_mode", lambda: True)
    registry_mod._check_fn_cache.clear()
    registry_mod._check_fn_last_good.clear()
    _avail, unavailable = registry_mod.registry.check_tool_availability()
    by_name = {i["name"]: i for i in unavailable}
    for ts in ("browser", "browser-cdp"):
        assert ts in by_name, sorted(by_name)
        assert by_name[ts]["superseded"] is True, by_name[ts]
