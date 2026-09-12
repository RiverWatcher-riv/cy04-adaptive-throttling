from dataclasses import FrozenInstanceError

import pandas as pd
import pytest

from cy04.baselines import always_allow, static_rate_threshold
from cy04.config import SEED
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


def test_apply_actions_rejects_extraneous_action_row():
    """A phantom (second, client_id) in `actions` that doesn't exist in
    `traffic` must be caught, not silently dropped -- this is the
    complementary failure mode to a missing action (e.g. a policy that
    computed actions for the wrong client set)."""
    extra = pd.concat(
        [ACTIONS, pd.DataFrame([{"second": 99, "client_id": 999, "action": "BLOCK"}])],
        ignore_index=True,
    )
    with pytest.raises(ValueError, match="not present in traffic"):
        apply_actions(TRAFFIC, extra)


def test_apply_actions_rejects_duplicate_action_row():
    """Two actions for the same (second, client_id) must raise a clear,
    specific error -- not an opaque pandas MergeError."""
    dup = pd.concat(
        [ACTIONS, pd.DataFrame([{"second": 0, "client_id": 0, "action": "BLOCK"}])],
        ignore_index=True,
    )
    with pytest.raises(ValueError, match="more than one action"):
        apply_actions(TRAFFIC, dup)


def test_apply_actions_rejects_unknown_client_class():
    """An unrecognized client_class must fail with a clear message at
    the point of the actual problem, not downstream as a cryptic
    IntCastingNaNError from an unmapped-and-NaN'd cost lookup."""
    bad_traffic = TRAFFIC.copy()
    bad_traffic.loc[0, "client_class"] = "mystery_class"
    with pytest.raises(ValueError, match="unknown client_class"):
        apply_actions(bad_traffic, ACTIONS)


def test_apply_actions_throttle_cost_uses_class_cost_per_request():
    """THROTTLE floors admitted *requests* to 1, then costs that one
    request at its class's cost_per_request -- not a flat 1. This is
    the case that matters most for low-and-slow (cost_per_request=5)."""
    low_slow_traffic = pd.DataFrame(
        [{"second": 0, "client_id": 5, "client_class": "low_and_slow_attacker", "requests": 4, "cost": 20}]
    )
    low_slow_actions = pd.DataFrame([{"second": 0, "client_id": 5, "action": "THROTTLE"}])
    admitted = apply_actions(low_slow_traffic, low_slow_actions)
    assert admitted.iloc[0]["admitted_requests"] == 1
    assert admitted.iloc[0]["admitted_cost"] == 5


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


# --- Multi-seed robustness + structural checks -----------------------------


@pytest.mark.parametrize("seed", [SEED, SEED + 1, SEED + 2, 1, 42])
def test_always_allow_overload_free_stays_near_zero_across_seeds(seed):
    """The dev-seed finding that unthrottled traffic breaches the 220 cap
    on ~every second isn't a dev-seed quirk -- it must hold for any seed
    drawn from the same class-generation rules, since Phase 5's
    generalization check depends on the underlying traffic shape being
    stable across seeds."""
    traffic = generate_traffic(seed)
    s = score(traffic, always_allow(traffic))
    assert s.overload_free < 0.05


def test_admitted_columns_are_integer_dtype_not_float():
    """Guards against the outer-merge-with-indicator implementation
    upcasting requests/cost to float64 (pandas does this defensively for
    any outer merge, whether or not this particular pair of frames
    actually has unmatched keys) and that upcast leaking into the public
    admitted_requests/admitted_cost columns."""
    traffic = generate_traffic(SEED)
    admitted = apply_actions(traffic, always_allow(traffic))
    assert admitted["admitted_requests"].dtype == "int64"
    assert admitted["admitted_cost"].dtype == "int64"


def test_apply_actions_preserves_traffic_row_order():
    traffic = generate_traffic(SEED)
    actions = always_allow(traffic)
    admitted = apply_actions(traffic, actions)
    assert (admitted["second"].to_numpy() == traffic["second"].to_numpy()).all()
    assert (admitted["client_id"].to_numpy() == traffic["client_id"].to_numpy()).all()


def test_score_is_immutable():
    """Asserts the specific FrozenInstanceError rather than a blind
    Exception -- a bare `pytest.raises(Exception)` would also pass if
    the assignment failed for some entirely unrelated reason, which
    would make this test look green while proving nothing."""
    s = score(TRAFFIC, ACTIONS)
    with pytest.raises(FrozenInstanceError):
        s.attack_prevention = 0.0  # type: ignore[misc]


def test_empty_traffic_returns_vacuous_perfect_scores():
    empty_traffic = TRAFFIC.iloc[0:0]
    empty_actions = pd.DataFrame(columns=["second", "client_id", "action"])
    admitted = apply_actions(empty_traffic, empty_actions)
    assert attack_prevention(admitted) == 1.0
    assert legitimate_admission(admitted) == 1.0
    assert overload_free(admitted) == 1.0
    assert legitimate_block_safety(admitted) == 1.0
