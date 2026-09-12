"""Phase 2 sanity report.

Runs both throwaway baselines through the real scoring harness and
prints all four axes plus the weighted total, per the Phase 2 exit
checklist: "confirm the harness scores both sensibly before writing the
real policy."
"""

from __future__ import annotations

from cy04.baselines import always_allow, static_rate_threshold
from cy04.config import SEED
from cy04.metrics import score
from cy04.simulator import generate_traffic


def _print_score(label: str, s) -> None:
    d = s.as_dict()
    print(f"-- {label} --")
    print(f"  AttackPrevention      = {d['AttackPrevention']:.4f}")
    print(f"  LegitimateAdmission   = {d['LegitimateAdmission']:.4f}")
    print(f"  OverloadFree          = {d['OverloadFree']:.4f}")
    print(f"  LegitimateBlockSafety = {d['LegitimateBlockSafety']:.4f}")
    print(f"  WeightedTotal         = {d['WeightedTotal']:.2f} / 95")
    print()


def main() -> None:
    traffic = generate_traffic(SEED)

    print(f"=== CY-04 Phase 2 sanity report (seed={SEED}) ===\n")

    _print_score("Baseline A: always-ALLOW", score(traffic, always_allow(traffic)))

    for threshold in (1.5, 3, 5):
        actions = static_rate_threshold(traffic, threshold=threshold)
        _print_score(f"Baseline B: static rate threshold = {threshold}", score(traffic, actions))


if __name__ == "__main__":
    main()
