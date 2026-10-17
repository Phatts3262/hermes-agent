"""Fork patch 2026-09-26 (review P-06): context_from injects the agent's answer, not the file head."""
from cron.scheduler import _MAX_CONTEXT_CHARS, _context_from_output


def test_response_section_is_injected_not_the_prompt_head():
    text = "PROMPT " * 500 + "\n## Response\n" + "The answer.\n\nSecond paragraph."
    assert _context_from_output(text) == "The answer.\n\nSecond paragraph."


def test_last_response_heading_wins_when_an_injected_context_carries_one():
    text = "prompt\n## Response\nOLD injected answer\n## Response\nNEW answer"
    assert _context_from_output(text) == "NEW answer"


def test_marker_less_output_contributes_its_tail_capped():
    text = "x" * (_MAX_CONTEXT_CHARS + 50) + "TAIL"
    out = _context_from_output(text)
    assert out.endswith("TAIL")
    assert out.startswith("[... output truncated ...]")
    assert len(out) <= _MAX_CONTEXT_CHARS + 40


def test_long_response_keeps_its_head_and_marks_truncation():
    text = "prompt\n## Response\n" + "HEAD" + "y" * (_MAX_CONTEXT_CHARS + 10)
    out = _context_from_output(text)
    assert out.startswith("HEAD")
    assert out.endswith("[... output truncated ...]")


def test_empty_output_is_empty():
    assert _context_from_output("") == ""


# --- e2e review 2026-10-17 B#1: walk back past skips, failures and [SILENT] ------------

import os  # noqa: E402

from cron.scheduler import (  # noqa: E402
    _CONTEXT_FROM_MAX_WALK,
    _latest_usable_context,
    _usable_context_from_output,
)

# The exact shapes cron/scheduler.py writes for a wake-gate skip, a failed run and a block.
_GATE_SKIP = (
    "# Cron Job: EOD\n\n**Job ID:** b8b28c9bdaed\n**Run Time:** 2026-10-14 18:53:19\n\n"
    "Script gate returned `wakeAgent=false` — agent skipped.\n"
)
_FAILED = (
    "# Cron Job: Intel (FAILED)\n\n**Job ID:** f09549dc0d76\n**Run Time:** 2026-10-16 15:03:16\n"
    "**Schedule:** 0 12,15 * * 1-5\n\n## Prompt\n\nraw market numbers 1.23 4.56\n\n"
    "## Error\n\n```\nRuntimeError: HTTP 429\n```\n"
)
_BLOCKED = "# Cron Job: X\n\n**Job ID:** abc\n**Run Time:** t\n**Status:** BLOCKED\n\nscanner said no\n"


def _answered(answer: str) -> str:
    return (
        "# Cron Job: EOD\n\n**Job ID:** b8b28c9bdaed\n**Run Time:** 2026-10-13 16:20:00\n"
        "**Schedule:** 15 16 * * 1-5\n\n## Prompt\n\nprompt mentions BLOCKED and (FAILED)\n\n"
        f"## Response\n\n{answer}\n"
    )


def _write(folder, name, text, mtime):
    path = folder / name
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_gate_skip_failure_block_and_silent_answers_are_unusable():
    assert _usable_context_from_output(_GATE_SKIP) == ""
    assert _usable_context_from_output(_FAILED) == ""
    assert _usable_context_from_output(_BLOCKED) == ""
    assert _usable_context_from_output(_answered("[SILENT]")) == ""
    assert _usable_context_from_output(_answered("(No response generated)")) == ""


def test_body_words_never_disqualify_a_real_answer():
    assert _usable_context_from_output(_answered("EOD: 3 fills, BLOCKED nothing")) == (
        "EOD: 3 fills, BLOCKED nothing"
    )


def test_walks_back_to_the_newest_run_that_answered(tmp_path):
    _write(tmp_path, "a.md", _answered("REAL EOD REVIEW"), 1000)
    _write(tmp_path, "b.md", _answered("[SILENT]"), 2000)
    _write(tmp_path, "c.md", _FAILED, 3000)
    _write(tmp_path, "d.md", _GATE_SKIP, 4000)
    assert _latest_usable_context(tmp_path) == "REAL EOD REVIEW"


def test_walk_is_capped(tmp_path):
    _write(tmp_path, "old.md", _answered("TOO OLD"), 1)
    for i in range(_CONTEXT_FROM_MAX_WALK):
        _write(tmp_path, f"skip{i}.md", _GATE_SKIP, 100 + i)
    assert _latest_usable_context(tmp_path) == ""
