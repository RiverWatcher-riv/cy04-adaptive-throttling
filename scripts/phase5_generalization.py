"""Phase 5 -- generalization check.

Per the build guide: run the Phase 4 FINAL configuration, UNCHANGED,
against 2-3 additional seeds never touched during tuning, and compare
all four scores. This is insurance against having tuned to quirks of
the dev seed's particular burst placements or lambda draws.

These seeds are deliberately disjoint from every seed used anywhere in
Phase 3/4 (dev seed and SEED+1..+4, plus 1, 42, 999999 from the
tuning-loop and safety sweeps) -- a genuinely unseen check, not a
re-read of numbers already in hand.
"""

from __future__ import annotations

from cy04.config import SEED
from cy04.metrics import score
from cy04.policy import PolicyParams
from cy04.run_eval import run_policy
from cy04.simulator import generate_traffic

UNSEEN_SEEDS = [7, 12345, 2027010100]
ALREADY_USED = {SEED, SEED + 1, SEED + 2, SEED + 3, SEED + 4, 1, 42, 999999}


def main() -> None:
    assert not (set(UNSEEN_SEEDS) & ALREADY_USED), "seed overlap with Phase 3/4 -- pick fresh ones"

    params = PolicyParams()  # frozen Phase 4 final config -- zero changes
    print("=== CY-04 Phase 5 generalization check ===")
    print(f"Seeds: {UNSEEN_SEEDS} (none used anywhere in Phase 3/4)\n")

    rows = []
    for seed in [SEED] + UNSEEN_SEEDS:
        traffic = generate_traffic(seed)
        log = run_policy(traffic, params)
        s = score(traffic, log[["second", "client_id", "action"]])

        legit = log[log["client_class"].isin(["normal_legit", "bursty_legit"])]
        legit_blocked = int((legit["action"] == "BLOCK").sum())
        atk = log[log["client_class"].isin(["sustained_attacker", "low_and_slow_attacker"])]
        atk_never_blocked = int(
            atk.groupby("client_id")
            .apply(lambda g: (g["action"] == "BLOCK").sum() == 0, include_groups=False)
            .sum()
        )

        tag = "(dev)" if seed == SEED else "(unseen)"
        rows.append(s)
        print(
            f"seed={seed:<12} {tag:10s} AP={s.attack_prevention:.4f} LA={s.legitimate_admission:.4f} "
            f"OF={s.overload_free:.4f} LBS={s.legitimate_block_safety:.4f} "
            f"TOTAL={s.weighted_total:.2f}  legitBLOCK={legit_blocked}  "
            f"attackersNeverBlocked={atk_never_blocked}/50"
        )

    print("\n-- Variance across the 3 unseen seeds (dev seed excluded) --")
    unseen_scores = rows[1:]
    for attr, label in [
        ("attack_prevention", "AttackPrevention"),
        ("legitimate_admission", "LegitimateAdmission"),
        ("overload_free", "OverloadFree"),
        ("legitimate_block_safety", "LegitimateBlockSafety"),
        ("weighted_total", "WeightedTotal"),
    ]:
        values = [getattr(s, attr) for s in unseen_scores]
        spread = max(values) - min(values)
        mean = sum(values) / len(values)
        print(f"  {label:24s} mean={mean:.4f}  range=[{min(values):.4f}, {max(values):.4f}]  spread={spread:.4f}")


if __name__ == "__main__":
    main()
