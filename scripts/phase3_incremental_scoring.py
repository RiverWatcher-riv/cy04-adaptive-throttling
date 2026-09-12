"""Phase 3 incremental build verification -- strictly per the build guide.

The Build Phases note requires the policy be built layer by layer with
a REAL score recorded after each addition ("Build incrementally. Score
after *each* addition -- this is what makes the eventual error analysis
and the 'which mechanism bought which points' narrative possible,
rather than a single opaque jump from baseline to final.") This script
produces that evidence against genuine partial configurations (via
max_layer), not a reconstruction after the fact.
"""

from __future__ import annotations

from cy04.baselines import always_allow
from cy04.config import SEED
from cy04.metrics import score
from cy04.policy import PolicyParams
from cy04.run_eval import run_policy
from cy04.simulator import generate_traffic

STEP_DESCRIPTIONS = {
    1: "Signals only, no decision logic (always ALLOW)",
    2: "+ cost-ratio detector only (L2, isolated)",
    3: "+ trust/persistence-gated rate anomaly (THROTTLE only, no BLOCK)",
    4: "+ full escalation ladder (BLOCK enabled) -- no capacity guard",
    5: "+ capacity guard (the complete policy)",
}

# Run the attribution twice, against two explicitly-pinned configs.
#
# The Phase 3 config is pinned rather than read from PolicyParams()
# defaults because Phase 4 later adopted three changes INTO those
# defaults -- so reading them would silently re-label post-tuning
# numbers as the Phase 3 record, and the recorded table would stop
# reproducing from its own script. Showing both is strictly more
# informative anyway: it demonstrates the per-layer attribution holds
# under both parameter sets, not just the one it was first measured on.
PHASE3_PARAMS = PolicyParams(
    absolute_rate_threshold=2.5,
    throttle_persistence=2,
    block_persistence_fast=8,
)
FINAL_PARAMS = PolicyParams()  # the shipped, Phase 4-tuned config


def _run_attribution(traffic, params, label: str, baseline_score):
    print(f"\n=== Per-layer attribution: {label} ===")
    rows = []
    log = None
    for layer in range(1, 6):
        log = run_policy(traffic, params, max_layer=layer)
        s = score(traffic, log[["second", "client_id", "action"]])
        rows.append((layer, s))
        _print_row(f"Step {layer}: {STEP_DESCRIPTIONS[layer]}", s)

    print("  -- Marginal contribution of each step (weighted total) --")
    prev = baseline_score.weighted_total
    for layer, s in rows:
        print(f"    step {layer}: {prev:6.2f} -> {s.weighted_total:6.2f}  (+{s.weighted_total - prev:.2f})")
        prev = s.weighted_total
    return log


def main() -> None:
    traffic = generate_traffic(SEED)

    print(f"=== CY-04 Phase 3 incremental scoring (seed={SEED}) ===\n")
    print("Reference: Baseline A (always-ALLOW) from Phase 2")
    baseline_score = score(traffic, always_allow(traffic))
    _print_row("Baseline A", baseline_score)

    _run_attribution(traffic, PHASE3_PARAMS, "Phase 3 config (as originally recorded)", baseline_score)
    log = _run_attribution(traffic, FINAL_PARAMS, "final shipped config (Phase 4-tuned)", baseline_score)

    print("\n-- Step-1 signal-behavior check (does NOT depend on scoring) --")
    settled = log[log["second"] >= 60]
    sig = settled.groupby("client_class")[["rate_ewma_fast", "cost_ewma", "cost_ratio"]].mean()
    print(sig.round(3).to_string())
    print(
        "\nlow_and_slow's cost_ewma/rate_ewma_fast ratio should be ~5x; "
        "all other classes should be ~1x (cost_ratio column above)."
    )


def _print_row(label: str, s) -> None:
    d = s.as_dict()
    print(
        f"  {label:55s} AP={d['AttackPrevention']:.3f} LA={d['LegitimateAdmission']:.3f} "
        f"OF={d['OverloadFree']:.3f} LBS={d['LegitimateBlockSafety']:.3f} "
        f"TOTAL={d['WeightedTotal']:.2f}"
    )


if __name__ == "__main__":
    main()
