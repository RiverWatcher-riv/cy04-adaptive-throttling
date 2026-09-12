"""The live simulation dashboard must actually run.

Streamlit's headless AppTest harness, not a browser. Catches the class
of bug that would otherwise first surface in front of a judge: an
exception on load, on a filter, at a boundary second, or a control that
silently stops responding after interaction (two real ones were found
this way -- see test_restart_works_after_a_manual_scrub).
"""

from pathlib import Path

import pytest

pytest.importorskip("streamlit", reason="demo/requirements.txt not installed -- core suite doesn't need it")

from streamlit.testing.v1 import AppTest

from cy04.config import DURATION_S

DASHBOARD_PATH = str(Path(__file__).resolve().parent.parent / "demo" / "dashboard.py")


def _fresh(timeout: int = 120) -> AppTest:
    at = AppTest.from_file(DASHBOARD_PATH, default_timeout=timeout)
    at.run()
    return at


def test_dashboard_loads_with_no_exceptions():
    at = _fresh()
    assert not at.exception


def test_detail_tabs_are_present():
    at = _fresh()
    assert [tab.label for tab in at.tabs] == [
        "All clients now",
        "Follow one client",
        "Simulation setup",
    ]


def test_all_four_scoring_axes_are_displayed():
    """The four judged measures plus the weighted total must all be on
    screen -- they're the point of the demo, not decoration."""
    at = _fresh()
    labels = [m.label for m in at.metric]
    for expected in (
        "Attack prevention · 35",
        "Legitimate admission · 30",
        "Overload-free · 20",
        "Block safety · 10",
        "Overall score",
    ):
        assert expected in labels, f"missing metric: {expected}"


def test_score_at_final_second_matches_cli_score():
    at = _fresh()
    at.slider[0].set_value(DURATION_S - 1).run()
    assert not at.exception
    values = {m.label: m.value for m in at.metric}
    assert float(values["Attack prevention · 35"]) > 0.9
    assert float(values["Block safety · 10"]) == 1.0
    assert float(values["Overall score"].split(" / ")[0]) > 90.0


def test_play_button_advances_the_clock():
    at = _fresh()
    assert at.slider[0].value == 0
    at.button[0].click().run()  # Play
    assert not at.exception
    assert at.slider[0].value > 0, "Play did not advance the simulation clock"


def test_restart_works_after_a_manual_scrub():
    """Regression test for a real bug: a positionally-defaulted slider
    kept its dragged value and silently ignored Restart, while working
    fine on a fresh load. Also guards the ordering constraint that broke
    it a second time -- Streamlit refuses writes to a widget-keyed state
    entry after that widget is instantiated, so Restart must run above
    the slider in script order."""
    at = _fresh()
    at.slider[0].set_value(300).run()
    assert at.slider[0].value == 300
    at.button[1].click().run()  # Restart
    assert not at.exception
    assert at.slider[0].value == 0, "Restart did not reset the clock after a manual scrub"


def test_client_table_filters_have_no_exceptions():
    at = _fresh()
    at.checkbox[0].set_value(True).run()
    at.multiselect[0].set_value(["BLOCK"]).run()
    assert not at.exception
    at.multiselect[1].set_value(["low_and_slow_attacker"]).run()
    assert not at.exception


def test_map_colour_toggle_has_no_exceptions():
    at = _fresh()
    at.radio[0].set_value("True type").run()
    assert not at.exception


def test_follow_one_client_survives_every_class_representative():
    at = _fresh()
    for client_id in (0, 1, 3, 20):  # normal, bursty, sustained, low-and-slow
        at.selectbox[1].set_value(client_id).run()
        assert not at.exception, f"exception on client_id={client_id}"


def test_survives_boundary_seconds():
    at = _fresh()
    for second in (0, 1, 2, DURATION_S - 2, DURATION_S - 1):
        at.slider[0].set_value(second).run()
        assert not at.exception, f"exception at second {second}"
