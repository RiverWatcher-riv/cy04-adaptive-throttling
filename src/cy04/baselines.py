"""Phase 2 -- throwaway naive baselines.

Neither of these is the real policy (that's Phase 3). They exist purely
as calibration anchors for the scoring harness and as the "before"
numbers for the pitch's error-analysis narrative.
"""

from __future__ import annotations

import pandas as pd


def always_allow(traffic: pd.DataFrame) -> pd.DataFrame:
    """Every request admitted, regardless of class.

    Sanity anchor: gives an upper bound on LegitimateAdmission (1.0) and
    LegitimateBlockSafety (1.0, nothing is ever blocked), and a lower
    bound on AttackPrevention (0.0, nothing is ever denied).
    """
    actions = traffic[["second", "client_id"]].copy()
    actions["action"] = "ALLOW"
    return actions


def static_rate_threshold(traffic: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """A single global instantaneous rate threshold: BLOCK any
    (second, client) whose *that-second* request count exceeds
    `threshold`; ALLOW everything else. No history, no persistence, no
    cost awareness -- the naive limiter the project's threat-model note
    argues cannot work at any threshold value:

      - threshold below ~6  -> catches bursty-legit's burst (lambda=6)
        before it ever catches the sustained attacker (lambda=4)
      - threshold above ~1.5 -> the low-and-slow attacker (lambda=1.5)
        passes through untouched regardless of where the line sits
    """
    actions = traffic[["second", "client_id"]].copy()
    actions["action"] = "BLOCK"
    actions.loc[traffic["requests"].to_numpy() <= threshold, "action"] = "ALLOW"
    return actions
