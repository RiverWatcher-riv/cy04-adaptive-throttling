import pandas as pd
import pytest

from cy04.config import SEED, ClientClass
from cy04.baselines import always_allow, static_rate_threshold
from cy04.metrics import (
    apply_actions,
    attack_prevention,
    legitimate_admission,
    legitimate_block_safety,
    overload_free,
    score,
)
from cy04.simulator import generate_traffic

# --- Hand-computed unit tests, independent of the simulator -----------------
#
# second 0: normal_legit (id 0) sends 2 req, ALLOWed -> admitted 2/2
#           sustained_attacker (id 1) sends 3 req, THROTTLEd -> admitted 1/3
# second 1: normal_legit (id 0) sends 1 req, BLOCKed -> admitted 0/1 (legit block!)
#           sustained_attacker (id 1) sends 5 req, BLOCKed -> admitted 0/5

TRAFFIC = pd.DataFrame(
    [
        {"second": 0, "client_id": 0, "client_class": "normal_legit", "requests": 2, "cost": 2},
        {"second": 0, "client_id": 1, "client_class": "sustained_attacker", "requests": 3, "cost": 3},
        {"second": 1, "client_id": 0, "client_class": "normal_legit", "requests": 1, "cost": 1},
        {"second": 1, "client_id": 1, "client_class": "sustained_attacker", "requests": 5, "cost": 5},
    ]
)

ACTIONS = pd.DataFrame(
    [
        {"second": 0, "client_id": 0, "action": "ALLOW"},
        {"second": 0, "client_id": 1, "action": "THROTTLE"},
        {"second": 1, "client_id": 0, "action": "BLOCK"},
        {"second": 1, "client_id": 1, "action": "BLOCK"},
    ]
)


def test_apply_actions_admitted_requests_and_cost():
    admitted = apply_actions(TRAFFIC, ACTIONS)
    expected = {
        (0, 0): (2, 2),  # ALLOW: full admit
        (0, 1): (1, 1),  # THROTTLE: floor to 1 request, cost 1 (cost/req = 1)
        (1, 0): (0, 0),  # BLOCK: nothing admitted
        (1, 1): (0, 0),  # BLOCK: nothing admitted
    }
    for _, row in admitted.iterrows():
        key = (row["second"], row["client_id"])
        assert (row["admitted_requests"], row["admitted_cost"]) == expected[key]


def test_apply_actions_rejects_missing_action():
    incomplete = ACTIONS.iloc[:-1]  # drop the last row
    with pytest.raises(ValueError, match="no assigned action"):
        apply_actions(TRAFFIC, incomplete)


def test_apply_actions_rejects_unknown_action():
    bad = ACTIONS.copy()
    bad.loc[0, "action"] = "DENY"  # not a real action
    with pytest.raises(ValueError, match="unknown action"):
        apply_actions(TRAFFIC, bad)


def test_hand_computed_axis_values():
    admitted = apply_actions(TRAFFIC, ACTIONS)

    # legit cost: generated 2+1=3, admitted 2+0=2 -> 2/3
    assert legitimate_admission(admitted) == pytest.approx(2 / 3)

    # attacker cost: generated 3+5=8, admitted 1+0=1 -> 1 - 1/8
    assert attack_prevention(admitted) == pytest.approx(1 - 1 / 8)

    # both seconds' admitted cost (3 and 0) are far under the real 220 cap
    assert overload_free(admitted) == pytest.approx(1.0)

    # legit client-seconds: 2 total (client 0 at sec 0 and sec 1), 1 blocked (sec 1)
    assert legitimate_block_safety(admitted) == pytest.approx(1 - 1 / 2)


def test_overload_free_with_custom_cap_detects_overshoot():
    # second 0 admits cost 3 (2 + 1), second 1 admits cost 0 -- with a cap
    # of 2, second 0 breaches it and second 1 doesn't.
    admitted = apply_actions(TRAFFIC, ACTIONS)
    assert overload_free(admitted, cap=2) == pytest.approx(0.5)


def test_axes_are_clipped_to_unit_interval():
    admitted = apply_actions(TRAFFIC, ACTIONS)
    for fn in (attack_prevention, legitimate_admission, overload_free, legitimate_block_safety):
        value = fn(admitted)
        assert 0.0 <= value <= 1.0


def test_weighted_total_matches_manual_sum():
    admitted = apply_actions(TRAFFIC, ACTIONS)
    s = score(TRAFFIC, ACTIONS)
    manual = (
        35 * attack_prevention(admitted)
        + 30 * legitimate_admission(admitted)
        + 20 * overload_free(admitted)
        + 10 * legitimate_block_safety(admitted)
    )
    assert s.weighted_total == pytest.approx(manual)
    assert s.as_dict()["WeightedTotal"] == pytest.approx(manual)


def test_no_attacker_traffic_is_vacuously_perfect_prevention():
    legit_only = TRAFFIC[TRAFFIC["client_class"] == "normal_legit"]
    legit_only_actions = ACTIONS[ACTIONS["client_id"] == 0]
    admitted = apply_actions(legit_only, legit_only_actions)
    assert attack_prevention(admitted) == 1.0


# --- Baseline sanity checks, against the real simulator ---------------------


def test_always_allow_baseline_scores_as_expected():
    traffic = generate_traffic(SEED)
    actions = always_allow(traffic)
    s = score(traffic, actions)

    assert s.legitimate_admission == pytest.approx(1.0)
    assert s.legitimate_block_safety == pytest.approx(1.0)
    assert s.attack_prevention == pytest.approx(0.0)
    # Established in Phase 1's own sanity report: unthrottled traffic
    # breaches the 220 cap on essentially every second of the run.
    assert s.overload_free < 0.05


def test_static_threshold_baseline_fails_in_both_documented_directions():
    traffic = generate_traffic(SEED)

    # A threshold below the bursty-legit burst lambda (6) but above the
    # low-and-slow attacker's lambda (1.5) should demonstrably both
    # (a) let meaningful low-and-slow attacker cost through, AND
    # (b) block real legitimate traffic (blowing LegitimateBlockSafety).
    actions = static_rate_threshold(traffic, threshold=3)
    s = score(traffic, actions)

    assert s.attack_prevention < 1.0  # low-and-slow cost partially escapes
    assert s.legitimate_block_safety < 1.0  # some legit client-seconds got blocked

    # A dumb limiter is not automatically worse everywhere -- it does
    # cut some attacker cost -- but it must not achieve a clean sweep.
    assert not (
        s.attack_prevention == pytest.approx(1.0)
        and s.legitimate_admission == pytest.approx(1.0)
        and s.legitimate_block_safety == pytest.approx(1.0)
    )


def test_static_threshold_hurts_bursty_legit_specifically():
    """The documented failure mode: a naive limiter's worst victim is a
    legitimate client's burst, not an attacker."""
    traffic = generate_traffic(SEED)
    actions = static_rate_threshold(traffic, threshold=3)
    admitted = apply_actions(traffic, actions)

    bursty = admitted[admitted["client_class"] == "bursty_legit"]
    normal = admitted[admitted["client_class"] == "normal_legit"]

    bursty_block_rate = (bursty["action"] == "BLOCK").mean()
    normal_block_rate = (normal["action"] == "BLOCK").mean()

    assert bursty_block_rate > normal_block_rate
