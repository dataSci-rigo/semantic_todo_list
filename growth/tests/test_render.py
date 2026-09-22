import pytest

from growth import render, steps


def test_escaping_and_keyboard_limits():
    assert render.esc("a < b & c") == "a &lt; b &amp; c"
    kb = render.kb([[("ok", "gb:1:x:1")]])
    assert kb["inline_keyboard"][0][0]["callback_data"] == "gb:1:x:1"
    with pytest.raises(ValueError):
        render.kb([[("too long", "gb:" + "x" * 70)]])


def test_render_text_step_has_skip_and_close():
    step = {"key": "aim", "type": "text", "skip": True}
    text, kb = steps.render_step(5, step, {}, None, "Done looks like <x>.", 1, 3, "Challenge")
    assert "&lt;x&gt;" in text and "1/3" in text
    labels = [b["text"] for row in kb["inline_keyboard"] for b in row]
    assert labels == ["Skip", "Close"]
