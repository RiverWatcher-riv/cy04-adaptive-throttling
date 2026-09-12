import numpy as np
import pandas as pd

from cy04.config import (
    BURST_COUNT,
    BURST_DURATION_S,
    BURST_START_RANGE,
    CLASS_SIZES,
    DURATION_S,
    LAMBDA_BURSTY_BURST,
    N_CLIENTS,
    SEED,
    ClientClass,
)
from cy04.simulator import build_clients, generate_traffic


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
