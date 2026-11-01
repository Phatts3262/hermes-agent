"""Tests for the CLI `/prompt` editor-compose command.

`/prompt` opens `$VISUAL`/`$EDITOR` on a temp markdown file so the user can
hand-edit a multi-line prompt, then queues the saved buffer as the next
agent turn via the one-shot `_pending_agent_seed` (same path `/blueprint`
uses). These drive a fake editor subprocess to verify read-back, header
stripping, seeding, and the empty-buffer cancel path.
"""

import shlex
import sys
from pathlib import Path

import pytest

from hermes_cli.cli_commands_mixin import CLICommandsMixin
from hermes_cli.commands import resolve_command


class _Stub(CLICommandsMixin):
    def __init__(self):
        self._pending_agent_seed = None


def _fake_editor(tmp_path: Path, body: str, mode: str = "append") -> str:
    """Return an ``$EDITOR`` value that mutates the file it is handed.

    The interpreter comes first, so the value is a real program on every host.
    A bare script path is not: Windows cannot start one.
    """
    script = tmp_path / "fake_editor.py"
    if mode == "append":
        text = body + "\n"
        script.write_text(
            "import sys\n"
            "with open(sys.argv[1], 'a', encoding='utf-8') as fh:\n"
            f"    fh.write({text!r})\n",
            encoding="utf-8",
        )
    else:  # clear
        script.write_text("import sys\nopen(sys.argv[1], 'w').close()\n", encoding="utf-8")
    return shlex.join([Path(sys.executable).as_posix(), script.as_posix()])


@pytest.fixture(autouse=True)
def _no_visual(monkeypatch):
    monkeypatch.delenv("VISUAL", raising=False)


def test_command_registered():
    cd = resolve_command("prompt")
    assert cd and cd.name == "prompt"
    assert resolve_command("compose").name == "prompt"


def test_compose_reads_and_strips_header(monkeypatch, tmp_path):
    monkeypatch.setenv("EDITOR", _fake_editor(tmp_path, "Refactor the auth module.\nUse pytest."))
    out = _Stub()._compose_in_editor("")
    assert "Refactor the auth module." in out
    assert "Use pytest." in out
    assert "#!" not in out  # the instructional header is stripped


def test_empty_buffer_does_not_seed(monkeypatch, tmp_path):
    monkeypatch.setenv("EDITOR", _fake_editor(tmp_path, "", mode="clear"))
    s = _Stub()
    s._handle_prompt_compose_command("/prompt")
    assert s._pending_agent_seed is None


def test_a_windows_editor_path_with_spaces_stays_one_program(tmp_path):
    folder = tmp_path / "Program Files"
    folder.mkdir()
    program = folder / "editor.exe"
    program.write_text("", encoding="utf-8")
    program.chmod(0o755)
    argv = _Stub._editor_argv(str(program), "note.md", is_windows=True)
    assert argv == [str(program), "note.md"]


@pytest.mark.windows_only
def test_a_script_windows_cannot_start_raises_and_never_reaches_the_shell(monkeypatch, tmp_path):
    script = tmp_path / "editor.sh"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("EDITOR", str(script))
    with pytest.raises(OSError):
        _Stub()._compose_in_editor("")
