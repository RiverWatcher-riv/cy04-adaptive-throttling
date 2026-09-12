"""Phase 1 -- traffic generator.

Builds the 200-client, 600-second synthetic traffic set: four classes,
fixed lambdas, non-overlapping bursts for the bursty-legit class, and
per-class cost-per-request. Reproducible: a given seed produces
byte-identical output on every run.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

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


@dataclass
class Client:
    client_id: int
    client_class: ClientClass
    base_lambda: float
    cost_per_request: int
    burst_windows: list[tuple[int, int]] = field(default_factory=list)

    def lambda_at(self, second: int) -> float:
        """Current-second lambda, honoring an active burst window if any."""
        if self.client_class == ClientClass.BURSTY_LEGIT:
            for start, end in self.burst_windows:
                if start <= second < end:
                    return LAMBDA_BURSTY_BURST
        return self.base_lambda


def _sample_burst_windows(rng: np.random.Generator) -> list[tuple[int, int]]:
    """Sample BURST_COUNT non-overlapping (start, end) windows.

    Starts are drawn from BURST_START_RANGE and resampled on overlap with
    any already-accepted window (including its full duration).
    """
    low, high = BURST_START_RANGE
    windows: list[tuple[int, int]] = []
    while len(windows) < BURST_COUNT:
        start = int(rng.integers(low, high + 1))
        end = start + BURST_DURATION_S
        if any(start < w_end and end > w_start for w_start, w_end in windows):
            continue
        windows.append((start, end))
    return sorted(windows)


def build_clients(rng: np.random.Generator) -> list[Client]:
    """Assign the 200 client ids to classes per CLASS_SIZES, shuffled."""
    labels: list[ClientClass] = []
    for cls, n in CLASS_SIZES.items():
        labels.extend([cls] * n)
    rng.shuffle(labels)

    clients = []
    for client_id, cls in enumerate(labels):
        burst_windows = (
            _sample_burst_windows(rng) if cls == ClientClass.BURSTY_LEGIT else []
        )
        clients.append(
            Client(
                client_id=client_id,
                client_class=cls,
                base_lambda=BASE_LAMBDA[cls],
                cost_per_request=COST_PER_REQUEST[cls],
                burst_windows=burst_windows,
            )
        )
    assert len(clients) == N_CLIENTS
    return clients


def client_roster(seed: int = SEED) -> list[Client]:
    """The client roster alone, without drawing any per-second traffic."""
    rng = np.random.Generator(np.random.PCG64(seed))
    return build_clients(rng)


def generate_traffic(seed: int = SEED) -> pd.DataFrame:
    """Generate the full traffic table for the run.

    Returns one row per (second, client) with columns:
    second, client_id, client_class, requests, cost.

    Deliberately materializes the full table for Phase 1 sanity-checking
    and offline analysis. Phase 3+ consumers MUST process this ordered by
    `second` and must never read a row with `second` greater than the
    current simulation clock -- that causal boundary (action chosen at t
    applies starting at t+1, never at t) is enforced in run_eval.py's
    loop, not by this data structure.
    """
    rng = np.random.Generator(np.random.PCG64(seed))
    clients = build_clients(rng)

    rows = []
    for second in range(DURATION_S):
        lambdas = np.array([c.lambda_at(second) for c in clients])
        requests = rng.poisson(lambdas)
        for client, n_req in zip(clients, requests):
            n_req = int(n_req)
            rows.append(
                {
                    "second": second,
                    "client_id": client.client_id,
                    "client_class": client.client_class.value,
                    "requests": n_req,
                    "cost": n_req * client.cost_per_request,
                }
            )
    return pd.DataFrame(rows)
