"""Phase 1 sanity report.

Run after any change to simulator.py or config.py. Prints per-class
totals and burst-window listings so the traffic generator's output can be
eyeballed as "boring and correct" before anything downstream depends on
it -- per the Phase 1 exit checklist.
"""

from __future__ import annotations

import numpy as np

from cy04.config import CAPACITY_CAP, SEED, ClientClass
from cy04.simulator import build_clients, generate_traffic


def main() -> None:
    df = generate_traffic(SEED)

    print(f"=== CY-04 Phase 1 sanity report (seed={SEED}) ===\n")

    print("-- Per-class totals --")
    totals = df.groupby("client_class").agg(
        clients=("client_id", "nunique"),
        total_requests=("requests", "sum"),
        total_cost=("cost", "sum"),
    )
    totals["avg_cost_per_request"] = totals["total_cost"] / totals["total_requests"]
    print(totals.to_string())
    print()

    print("-- Bursty-legit burst windows (first 5 clients) --")
    rng = np.random.Generator(np.random.PCG64(SEED))
    clients = build_clients(rng)
    bursty = [c for c in clients if c.client_class == ClientClass.BURSTY_LEGIT][:5]
    for c in bursty:
        print(f"  client {c.client_id}: {sorted(c.burst_windows)}")
    print()

    print("-- Aggregate admitted cost per second (raw, no policy applied) --")
    per_second = df.groupby("second")["cost"].sum()
    over_cap = (per_second > CAPACITY_CAP).sum()
    print(f"  min={per_second.min()} mean={per_second.mean():.1f} max={per_second.max()}")
    print(
        f"  seconds over the {CAPACITY_CAP} cap (informational only, "
        f"no policy yet): {over_cap} / {len(per_second)}"
    )


if __name__ == "__main__":
    main()
