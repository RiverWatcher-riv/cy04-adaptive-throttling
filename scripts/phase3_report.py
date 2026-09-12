"""Phase 3 sanity report.

Walks the Phase 3 exit checklist: per-class signal behaviour, the
cost-ratio detector in isolation, bursty-legit tolerance vs. sustained-
attacker escalation, the block-safety invariant, and how often the
capacity guard actually intervenes.
"""

from __future__ import annotations

import pandas as pd

from cy04.config import SEED
from cy04.metrics import score
from cy04.policy import PolicyParams
from cy04.run_eval import run_policy
from cy04.simulator import generate_traffic

LEGIT = ["normal_legit", "bursty_legit"]


def main() -> None:
    params = PolicyParams()
    traffic = generate_traffic(SEED)
    log = run_policy(traffic, params)

    print(f"=== CY-04 Phase 3 sanity report (seed={SEED}) ===\n")

    print("-- L1/L2 signals: mean per class (settled state, t >= 60) --")
    settled = log[log["second"] >= 60]
    sig = settled.groupby("client_class")[
        ["rate_ewma_fast", "rate_ewma_slow", "cost_ewma", "cost_ratio", "trust"]
    ].mean()
    print(sig.round(3).to_string())
    print()

    print("-- L2 in isolation: cost-ratio flag rate by class --")
    print("   (legit cost/request is exactly 1, so this path is closed to them)")
    flag = settled.groupby("client_class")["cost_ratio_flagged"].mean().mul(100).round(2)
    print(flag.to_string())
    print()

    print("-- L4 action mix by class (% of client-seconds) --")
    dist = log.groupby(["client_class", "action"]).size().unstack(fill_value=0)
    print(((dist.T / dist.sum(axis=1)).T * 100).round(2).to_string())
    print()

    print("-- INVARIANT: no legitimate client-second may be BLOCKed --")
    legit = log[log["client_class"].isin(LEGIT)]
    blocked = int((legit["action"] == "BLOCK").sum())
    print(f"   legit BLOCK client-seconds: {blocked}  ->  {'PASS' if blocked == 0 else 'FAIL'}")
    print(
        f"   worst legit anomalous streak: {int(legit['persistence_counter'].max())}s "
        f"(BLOCK duration bar: {params.block_persistence_slow}s)"
    )
    print()

    print("-- Attacker escalation: clients reaching BLOCK at least once --")
    for cls in ["sustained_attacker", "low_and_slow_attacker"]:
        sub = log[log["client_class"] == cls]
        hit = sub[sub["action"] == "BLOCK"]["client_id"].nunique()
        print(f"   {cls}: {hit}/{sub['client_id'].nunique()}")
    print()

    print("-- Bursty-legit recovery (decay-back to ALLOW after a burst) --")
    bursty = log[log["client_class"] == "bursty_legit"]
    throttled_pct = (bursty["action"] == "THROTTLE").mean() * 100
    print(f"   time THROTTLEd: {throttled_pct:.2f}%   time ALLOWed: {(bursty['action'] == 'ALLOW').mean() * 100:.2f}%")
    print()

    print("-- Final score --")
    s = score(traffic, log[["second", "client_id", "action"]])
    for key, value in s.as_dict().items():
        if key == "WeightedTotal":
            print(f"   {key:24s} {value:.2f} / 95")
        else:
            print(f"   {key:24s} {value:.4f}")


if __name__ == "__main__":
    main()
