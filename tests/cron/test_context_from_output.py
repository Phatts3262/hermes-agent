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
