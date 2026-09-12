"""Phase 6 -- the demo dashboard must actually run.

Uses Streamlit's headless AppTest harness rather than a browser. Not a
replacement for rehearsing the live demo, but it catches the class of
bug that would otherwise only surface for the first time in front of
judges: an exception on load, on toggling reveal, on scrubbing to the
end of the run, or on the jump-to-moment buttons.
"""

from pathlib import Path

import pytest

pytest.importorskip("streamlit", reason="demo/requirements.txt not installed -- core suite doesn't need it")

from streamlit.testing.v1 import AppTest

from cy04.config import DURATION_S

DASHBOARD_PATH = str(Path(__file__).resolve().parent.parent / "demo" / "dashboard.py")


def test_dashboard_loads_with_no_exceptions():
    at = AppTest.from_file(DASHBOARD_PATH, default_timeout=60)
    at.run()
    assert not at.exception


def test_reveal_toggle_has_no_exceptions():
    at = AppTest.from_file(DASHBOARD_PATH, default_timeout=60)
    at.run()
    at.toggle[0].set_value(True).run()
    assert not at.exception


def test_end_of_run_scorecard_matches_cli_score():
    at = AppTest.from_file(DASHBOARD_PATH, default_timeout=60)
    at.run()
    at.slider[0].set_value(DURATION_S - 1).run()
    assert not at.exception

    values = {m.label: m.value for m in at.metric}
    assert float(values["AttackPrevention (35)"]) > 0.9
    assert float(values["LegitimateBlockSafety (10)"]) == 1.0
    total = values["Weighted total"].split(" / ")[0]
    assert float(total) > 90.0


def test_jump_buttons_move_the_slider_to_the_rehearsed_moments():
    at = AppTest.from_file(DASHBOARD_PATH, default_timeout=60)
    at.run()
    bursty_btn = next(b for b in at.button if "bursty" in b.label.lower())
    bursty_btn.click().run()
    assert not at.exception
    assert at.slider[0].value == 265

    low_slow_btn = next(b for b in at.button if "low-and-slow" in b.label.lower())
    low_slow_btn.click().run()
    assert not at.exception
    assert at.slider[0].value == 100


def test_reset_and_jump_still_work_after_a_manual_scrub():
    """Regression test for a real demo-day bug. The slider was built with
    a positional default, so once the user had dragged it, Streamlit's
    retained widget state overrode the new default and Reset/Jump
    silently did nothing -- while still looking fine on a fresh load,
    which is why it went unnoticed. A presenter scrubs around before
    jumping to a moment, so this is the exact live sequence."""
    at = AppTest.from_file(DASHBOARD_PATH, default_timeout=60)
    at.run()
    at.slider[0].set_value(300).run()
    assert at.slider[0].value == 300

    next(b for b in at.button if b.label == "Reset").click().run()
    assert not at.exception
    assert at.slider[0].value == 0, "Reset did not move the slider after a manual scrub"

    at.slider[0].set_value(420).run()
    next(b for b in at.button if "bursty" in b.label.lower()).click().run()
    assert not at.exception
    assert at.slider[0].value == 265, "Jump did not move the slider after a manual scrub"


def test_demo_survives_boundary_seconds_and_every_speed_option():
    at = AppTest.from_file(DASHBOARD_PATH, default_timeout=60)
    at.run()
    for second in (0, 1, 2, DURATION_S - 2, DURATION_S - 1):
        at.slider[0].set_value(second).run()
        assert not at.exception, f"exception at second {second}"

    at.toggle[0].set_value(True).run()  # reveal on at the end of the run
    assert not at.exception

    at.slider[0].set_value(0).run()  # reveal on at t=0, all signals still zero
    assert not at.exception

    for option in at.selectbox[0].options:
        at.selectbox[0].set_value(option).run()
        assert not at.exception, f"exception at speed {option}"
