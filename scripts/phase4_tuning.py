"""Phase 4 -- tuning loop against the exact scoring formulas.

Per the build guide: run full sim -> score all four axes -> adjust one
or few parameters -> rerun -> record, as an explicit table. Two of the
15 parameters (block_persistence_slow, block_clean_history_max) are
excluded from tuning here on purpose -- they encode the structural
block-safety guarantee Phase 3 QA fixed after finding it fragile under
tuning pressure, and are not up for renegotiation for a fraction of a
point. Every other parameter is fair game.

Each iteration is scored against the dev seed AND two additional seeds,
so a change that only helps on 20260911 doesn't get mistaken for a real
improvement before Phase 5's dedicated generalization check.
"""

from __future__ import annotations

from dataclasses import replace

from cy04.config import SEED
from cy04.metrics import score
from cy04.policy import PolicyParams
from cy04.run_eval import run_policy
from cy04.simulator import generate_traffic

CHECK_SEEDS = [SEED, SEED + 1, 42]

# The tuning loop's starting point is the PHASE 3 FINAL config, pinned
# explicitly here rather than read from PolicyParams() defaults. This
# matters: Phase 4 adopted three of these changes INTO the defaults, so
# reading the baseline from the defaults would make iters 4.1/4.2/4.5
# no-ops against themselves and collapse the whole table to one
# repeated number -- i.e. the recorded results would stop reproducing
# from their own script. Pinning keeps this loop an honest record of
# the search that was actually run.
PHASE3_FINAL_PARAMS = PolicyParams(
    absolute_rate_threshold=2.5,
    throttle_persistence=2,
    block_persistence_fast=8,
)


def run_iteration(label: str, params: PolicyParams, notes: str = "") -> dict:
    scores = []
    legit_blocks = 0
    for seed in CHECK_SEEDS:
        traffic = generate_traffic(seed)
        log = run_policy(traffic, params)
        legit = log[log["client_class"].isin(["normal_legit", "bursty_legit"])]
        legit_blocks += int((legit["action"] == "BLOCK").sum())
        scores.append(score(traffic, log[["second", "client_id", "action"]]))

    avg = lambda attr: sum(getattr(s, attr) for s in scores) / len(scores)
    row = {
        "label": label,
        "attack_prevention": avg("attack_prevention"),
        "legitimate_admission": avg("legitimate_admission"),
        "overload_free": avg("overload_free"),
        "legitimate_block_safety": avg("legitimate_block_safety"),
        "weighted_total": avg("weighted_total"),
        "legit_blocks_across_seeds": legit_blocks,
        "notes": notes,
    }
    print(
        f"{label:60s} AP={row['attack_prevention']:.4f} LA={row['legitimate_admission']:.4f} "
        f"OF={row['overload_free']:.4f} LBS={row['legitimate_block_safety']:.4f} "
        f"TOTAL={row['weighted_total']:.2f}  legitBLOCK={legit_blocks}  {notes}"
    )
    return row


def main() -> None:
    print(f"=== CY-04 Phase 4 tuning loop (avg over seeds {CHECK_SEEDS}) ===\n")

    baseline = PHASE3_FINAL_PARAMS
    rows = [run_iteration("iter 4.0: Phase 3 final config (loop baseline)", baseline)]

    # --- Iter 4.1: react to the rate path faster -------------------------
    rows.append(
        run_iteration(
            "iter 4.1: throttle_persistence 2 -> 1",
            replace(baseline, throttle_persistence=1),
            "faster THROTTLE trigger on rate anomaly",
        )
    )

    # --- Iter 4.2: catch low-and-slow's fast BLOCK path sooner -----------
    rows.append(
        run_iteration(
            "iter 4.2: block_persistence_fast 8 -> 4",
            replace(baseline, block_persistence_fast=4),
            "cost-ratio path is near-zero-FP; shorten its bar",
        )
    )

    # --- Iter 4.3: combine 4.1 + 4.2 --------------------------------------
    rows.append(
        run_iteration(
            "iter 4.3: combine 4.1 + 4.2",
            replace(baseline, throttle_persistence=1, block_persistence_fast=4),
        )
    )

    # --- Iter 4.4: deliberate overshoot -- probe the AP/LA tension point --
    rows.append(
        run_iteration(
            "iter 4.4: trust_gate_threshold 0.5 -> 0.9 (AGGRESSIVE)",
            replace(baseline, trust_gate_threshold=0.9),
            "deliberately overshoots: much easier to fall below the gate",
        )
    )

    # --- Iter 4.5: a second overshoot probe, different mechanism ----------
    rows.append(
        run_iteration(
            "iter 4.5: absolute_rate_threshold 2.5 -> 1.8 (AGGRESSIVE)",
            replace(baseline, absolute_rate_threshold=1.8),
            "deliberately overshoots: closer to legit rates",
        )
    )

    # --- Iter 4.6: final adopted config -- every non-regressive win above -
    final = replace(
        baseline, throttle_persistence=1, block_persistence_fast=4, absolute_rate_threshold=1.8
    )
    rows.append(run_iteration("iter 4.6: FINAL (4.1 + 4.2 + 4.5 combined)", final))

    # Self-check against drift: iter 4.6 IS what ships. If someone edits
    # PolicyParams' defaults without rerunning this loop, say so loudly
    # rather than letting the recorded table quietly stop matching the
    # shipped policy (exactly the failure this script was found to have
    # during the final QA pass).
    shipped = PolicyParams()
    drifted = {
        field: (getattr(final, field), getattr(shipped, field))
        for field in vars(final)
        if getattr(final, field) != getattr(shipped, field)
    }
    if drifted:
        print("\n!! WARNING: iter 4.6 no longer matches the shipped PolicyParams defaults:")
        for field, (loop_value, shipped_value) in drifted.items():
            print(f"   {field}: loop={loop_value} shipped={shipped_value}")
    else:
        print("\n[self-check] iter 4.6 matches the shipped PolicyParams defaults exactly.")

    print("\n=== Tension-point analysis ===")
    d40, d44, d45 = rows[0], rows[4], rows[5]
    print(
        f"4.0 -> 4.4 (trust_gate_threshold 0.5->0.9): "
        f"AttackPrevention {d40['attack_prevention']:.4f} -> {d44['attack_prevention']:.4f} "
        f"({d44['attack_prevention']-d40['attack_prevention']:+.4f}), "
        f"LegitimateAdmission {d40['legitimate_admission']:.4f} -> {d44['legitimate_admission']:.4f} "
        f"({d44['legitimate_admission']-d40['legitimate_admission']:+.4f})"
    )
    print(
        f"4.0 -> 4.5 (absolute_rate_threshold 2.5->1.8): "
        f"AttackPrevention {d40['attack_prevention']:.4f} -> {d45['attack_prevention']:.4f} "
        f"({d45['attack_prevention']-d40['attack_prevention']:+.4f}), "
        f"LegitimateAdmission {d40['legitimate_admission']:.4f} -> {d45['legitimate_admission']:.4f} "
        f"({d45['legitimate_admission']-d40['legitimate_admission']:+.4f})"
    )


if __name__ == "__main__":
    main()
