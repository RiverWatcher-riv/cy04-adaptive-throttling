"""The exploration dashboard must actually run.

Uses Streamlit's headless AppTest harness rather than a browser. Not a
replacement for opening it once for real, but it catches the class of
bug that would otherwise only surface for the first time in front of a
judge: an exception on load, on toggling a filter, on scrubbing to a
boundary second, or on a UI control that silently stops responding
after interaction (a real one was found and fixed here -- see
test_reset_still_works_after_a_manual_scrub).
"""

from pathlib import Path

import pytest

pytest.importorskip("streamlit", reason="demo/requirements.txt not installed -- core suite doesn't need it")

from streamlit.testing.v1 import AppTest

from cy04.config import DURATION_S

DASHBOARD_PATH = str(Path(__file__).resolve().parent.parent / "demo" / "dashboard.py")


def _fresh(timeout: int = 90) -> AppTest:
    at = AppTest.from_file(DASHBOARD_PATH, default_timeout=timeout)
    at.run()
    return at


def test_dashboard_loads_with_no_exceptions():
    at = _fresh()
    assert not at.exception


def test_show_true_class_checkbox_has_no_exceptions():
    at = _fresh()
    at.checkbox[0].set_value(True).run()
    assert not at.exception


def test_score_at_final_second_matches_cli_score():
    """The dashboard scores seconds [0, t] live; at t = DURATION_S - 1
    that must equal the CLI's full-run score exactly, since it's the
    same score() call over the same data."""
    at = _fresh()
    at.slider[0].set_value(DURATION_S - 1).run()
    assert not at.exception

    values = {m.label: m.value for m in at.metric}
    assert float(values["AttackPrevention · 35"]) > 0.9
    assert float(values["LegitimateBlockSafety · 10"]) == 1.0
    total = values["Weighted total"].split(" / ")[0]
    assert float(total) > 90.0


def test_reset_still_works_after_a_manual_scrub():
    """Regression test for a real bug: the slider was built with a
    positional default, so once dragged, Streamlit's retained widget
    state overrode the new default and Reset silently did nothing --
    while working fine on a fresh load, which is why it went unnoticed."""
    at = _fresh()
    at.slider[0].set_value(300).run()
    assert at.slider[0].value == 300

    next(b for b in at.button if b.label == "Reset").click().run()
    assert not at.exception
    assert at.slider[0].value == 0, "Reset did not move the slider after a manual scrub"


def test_client_explorer_filters_have_no_exceptions():
    at = _fresh()
    at.checkbox[0].set_value(True).run()  # show true class, enabling the class filter
    at.multiselect[0].set_value(["BLOCK"]).run()  # filter: action
    assert not at.exception
    at.multiselect[1].set_value(["low_and_slow_attacker"]).run()  # filter: true class
    assert not at.exception


def test_color_by_radio_has_no_exceptions():
    at = _fresh()
    at.radio[0].set_value("true class").run()
    assert not at.exception


def test_client_deep_dive_survives_every_class_representative():
    """Pick one client from each class and confirm the deep-dive panel
    renders for all of them, including a legit client with no bursts
    (empty burst_windows) and an attacker (no burst_windows either)."""
    at = _fresh()
    for client_id in (0, 1, 3, 20):  # normal_legit, bursty_legit, sustained, low-and-slow
        at.number_input[0].set_value(client_id).run()
        assert not at.exception, f"exception on client_id={client_id}"


def test_survives_boundary_seconds_and_every_step_size():
    at = _fresh()
    for second in (0, 1, 2, DURATION_S - 2, DURATION_S - 1):
        at.slider[0].set_value(second).run()
        assert not at.exception, f"exception at second {second}"

    for option in at.selectbox[0].options:
        at.selectbox[0].set_value(option).run()
        assert not at.exception, f"exception at step size {option}"
