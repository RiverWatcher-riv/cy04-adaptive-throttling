import numpy as np
import pandas as pd
import pytest

from cy04.config import (
    BASE_LAMBDA,
    BURST_COUNT,
    BURST_DURATION_S,
    BURST_START_RANGE,
    CLASS_SIZES,
    COST_PER_REQUEST,
    DURATION_S,
    LAMBDA_BURSTY_BURST,
    N_CLIENTS,
    SEED,
    ClientClass,
)
from cy04.simulator import build_clients, client_roster, generate_traffic


def test_reproducible_across_runs():
    a = generate_traffic(SEED)
    b = generate_traffic(SEED)
    pd.testing.assert_frame_equal(a, b)


def test_client_roster_matches_class_sizes():
    rng = np.random.Generator(np.random.PCG64(SEED))
    clients = build_clients(rng)
    assert len(clients) == N_CLIENTS
    counts: dict[ClientClass, int] = {}
    for c in clients:
        counts[c.client_class] = counts.get(c.client_class, 0) + 1
    assert counts == CLASS_SIZES


def test_bursty_clients_have_exactly_three_nonoverlapping_windows():
    rng = np.random.Generator(np.random.PCG64(SEED))
    clients = build_clients(rng)
    bursty = [c for c in clients if c.client_class == ClientClass.BURSTY_LEGIT]
    assert len(bursty) == CLASS_SIZES[ClientClass.BURSTY_LEGIT]

    low, high = BURST_START_RANGE
    for c in bursty:
        assert len(c.burst_windows) == BURST_COUNT
        windows = sorted(c.burst_windows)
        for start, end in windows:
            assert end - start == BURST_DURATION_S
            assert low <= start <= high
        for (_, e1), (s2, _) in zip(windows, windows[1:]):
            assert e1 <= s2  # non-overlapping, back-to-back allowed


def test_lambda_switches_to_burst_value_only_during_burst_windows():
    rng = np.random.Generator(np.random.PCG64(SEED))
    clients = build_clients(rng)
    bursty = next(c for c in clients if c.client_class == ClientClass.BURSTY_LEGIT)
    in_burst = {s for start, end in bursty.burst_windows for s in range(start, end)}

    for second in range(0, DURATION_S, 7):  # sampled, not exhaustive, for speed
        expected = LAMBDA_BURSTY_BURST if second in in_burst else bursty.base_lambda
        assert bursty.lambda_at(second) == expected


def test_low_and_slow_costs_more_than_sustained_despite_fewer_requests():
    """The core inversion the whole policy is built around."""
    df = generate_traffic(SEED)
    by_class = df.groupby("client_class")[["requests", "cost"]].sum()

    low_slow = by_class.loc[ClientClass.LOW_AND_SLOW_ATTACKER.value]
    sustained = by_class.loc[ClientClass.SUSTAINED_ATTACKER.value]

    assert low_slow["requests"] < sustained["requests"]
    assert low_slow["cost"] > sustained["cost"]


def test_causal_shape_and_coverage():
    df = generate_traffic(SEED)
    assert list(df.columns) == ["second", "client_id", "client_class", "requests", "cost"]
    assert df["second"].min() == 0
    assert df["second"].max() == DURATION_S - 1
    assert df["client_id"].nunique() == N_CLIENTS
    assert len(df) == DURATION_S * N_CLIENTS


# --- Data integrity -----------------------------------------------------


def test_no_nulls_and_expected_dtypes():
    df = generate_traffic(SEED)
    assert df.isnull().sum().sum() == 0
    assert pd.api.types.is_integer_dtype(df["second"])
    assert pd.api.types.is_integer_dtype(df["client_id"])
    assert pd.api.types.is_integer_dtype(df["requests"])
    assert pd.api.types.is_integer_dtype(df["cost"])
    assert (df["requests"] >= 0).all()
    assert (df["cost"] >= 0).all()


def test_no_duplicate_second_client_pairs():
    df = generate_traffic(SEED)
    assert df.duplicated(subset=["second", "client_id"]).sum() == 0


def test_cost_equals_requests_times_cost_per_request_for_every_row():
    """Vectorized, exhaustive -- not sampled. Cost must never drift from
    requests * cost_per_request for the row's class, for any of the
    120,000 rows."""
    df = generate_traffic(SEED)
    expected_cost_per_class = {cls.value: COST_PER_REQUEST[cls] for cls in ClientClass}
    expected = df["requests"] * df["client_class"].map(expected_cost_per_class)
    assert (df["cost"] == expected).all()


# --- Cross-API consistency ------------------------------------------------


def test_client_roster_matches_generate_traffic_assignment():
    """client_roster() and generate_traffic() must agree on every client's
    class -- they build independent RNGs from the same seed and must not
    silently diverge (e.g. if one is refactored without the other)."""
    roster = {c.client_id: c.client_class.value for c in client_roster(SEED)}
    df = generate_traffic(SEED)
    df_classes = df.drop_duplicates("client_id").set_index("client_id")["client_class"].to_dict()
    assert roster == df_classes


def test_different_seeds_produce_different_traffic_and_rosters():
    df_a = generate_traffic(SEED)
    df_b = generate_traffic(SEED + 1)
    assert not df_a.equals(df_b)

    roster_a = [c.client_class for c in client_roster(SEED)]
    roster_b = [c.client_class for c in client_roster(SEED + 1)]
    assert roster_a != roster_b


# --- Burst boundary semantics, exhaustive across the full roster ----------


def test_burst_boundaries_are_start_inclusive_end_exclusive_for_every_bursty_client():
    roster = client_roster(SEED)
    bursty = [c for c in roster if c.client_class == ClientClass.BURSTY_LEGIT]
    assert len(bursty) == CLASS_SIZES[ClientClass.BURSTY_LEGIT]

    for c in bursty:
        for start, end in c.burst_windows:
            if start > 0:
                assert c.lambda_at(start - 1) == c.base_lambda
            assert c.lambda_at(start) == LAMBDA_BURSTY_BURST
            assert c.lambda_at(end - 1) == LAMBDA_BURSTY_BURST
            if end < DURATION_S:
                assert c.lambda_at(end) == c.base_lambda


# --- Statistical validation -----------------------------------------------


@pytest.mark.parametrize("cls", list(ClientClass))
def test_empirical_mean_rate_matches_theoretical_lambda(cls):
    """Sample mean request rate per class should sit within a generous
    z-score band of the theoretical (or, for bursty-legit, time-weighted
    mixture) lambda. A z well outside this band indicates a broken
    sampling path (wrong lambda wired up, burst fraction miscomputed,
    etc.), not sampling noise -- 5 sigma on a 12k-120k sample size is
    effectively never crossed by chance."""
    df = generate_traffic(SEED)
    sub = df[df["client_class"] == cls.value]
    n_samples = len(sub)
    assert n_samples == CLASS_SIZES[cls] * DURATION_S

    if cls == ClientClass.BURSTY_LEGIT:
        frac_burst = (BURST_COUNT * BURST_DURATION_S) / DURATION_S
        expected_lambda = (1 - frac_burst) * BASE_LAMBDA[cls] + frac_burst * LAMBDA_BURSTY_BURST
        variance_bound = LAMBDA_BURSTY_BURST  # conservative upper bound for the mixture
    else:
        expected_lambda = BASE_LAMBDA[cls]
        variance_bound = expected_lambda

    empirical_mean = sub["requests"].mean()
    standard_error = (variance_bound / n_samples) ** 0.5
    z = abs(empirical_mean - expected_lambda) / standard_error
    assert z < 5, f"{cls.value}: z={z:.2f}, mean={empirical_mean:.4f}, expected={expected_lambda:.4f}"
