"""Phase 2 -- scoring harness.

Implements the four axes exactly as defined in the project's Scoring
Model note (authored in-house -- no official CY-04 formula sheet is
available, and a sub-4-hour budget rules out waiting for one):

    AttackPrevention      = clip(1 - attacker_cost_admitted / attacker_cost_generated, 0, 1)
    LegitimateAdmission   = clip(legit_cost_admitted / legit_cost_generated,           0, 1)
    OverloadFree          = clip(seconds_under_cap / total_seconds,                    0, 1)
    LegitimateBlockSafety = clip(1 - legit_client_seconds_blocked / legit_client_seconds_total, 0, 1)

    WeightedTotal = 35*AttackPrevention + 30*LegitimateAdmission
                  + 20*OverloadFree + 10*LegitimateBlockSafety   # out of 95

Both AttackPrevention and LegitimateAdmission are cost-weighted, not
request-count-weighted -- the project's own framing ("starve attackers of
cost") is explicitly about cost, and the low-and-slow attacker class only
matters at all if cost is what's being measured.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from cy04.config import ATTACKER_CLASSES, CAPACITY_CAP, COST_PER_REQUEST, LEGIT_CLASSES, ClientClass

ACTIONS = ("ALLOW", "THROTTLE", "BLOCK")

_ATTACKER_VALUES = {c.value for c in ATTACKER_CLASSES}
_LEGIT_VALUES = {c.value for c in LEGIT_CLASSES}
_COST_PER_REQUEST_BY_VALUE = {c.value: COST_PER_REQUEST[c] for c in ClientClass}


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def apply_actions(traffic: pd.DataFrame, actions: pd.DataFrame) -> pd.DataFrame:
    """Merge traffic with per-(second, client) actions and compute what
    actually gets admitted under each action's semantics:

      ALLOW    -> admit every generated request
      THROTTLE -> admit at most 1 request (floor; never fabricates a
                  request that didn't arrive -- a client with 0 requests
                  that second still gets 0 admitted while throttled)
      BLOCK    -> admit nothing

    Every (second, client_id) pair in `traffic` must have exactly one
    matching row in `actions`, and `actions` must not contain any pair
    absent from `traffic` -- a missing, duplicated, or extraneous action
    is a bug in the caller (e.g. a policy that skipped a client, double-
    counted one, or computed actions for the wrong client set) and is
    raised with a clear, specific message, not silently dropped or left
    to surface as an opaque pandas/numpy error downstream.
    """
    dup_mask = actions.duplicated(subset=["second", "client_id"], keep=False)
    if dup_mask.any():
        dupes = actions.loc[dup_mask, ["second", "client_id"]].drop_duplicates()
        raise ValueError(
            f"{len(dupes)} (second, client_id) pair(s) have more than one action "
            f"assigned (e.g. {dupes.iloc[0].to_dict()})"
        )

    # Single outer merge, vectorized: the `_merge` indicator does the
    # missing/extra key-set check without materializing Python-level sets
    # of tuples (which, on a 120k-row traffic table, cost ~300ms and
    # dominated apply_actions's runtime). Once validated, this same merge
    # result -- not a second merge -- is reused for the actual computation.
    merged = traffic.merge(
        actions, on=["second", "client_id"], how="outer", indicator=True
    )

    missing_mask = merged["_merge"] == "left_only"
    if missing_mask.any():
        sample = list(
            merged.loc[missing_mask, ["second", "client_id"]].head(3).itertuples(index=False, name=None)
        )
        raise ValueError(
            f"{int(missing_mask.sum())} (second, client_id) row(s) in traffic have no "
            f"assigned action (e.g. {sample})"
        )

    extra_mask = merged["_merge"] == "right_only"
    if extra_mask.any():
        sample = list(
            merged.loc[extra_mask, ["second", "client_id"]].head(3).itertuples(index=False, name=None)
        )
        raise ValueError(
            f"actions table has {int(extra_mask.sum())} (second, client_id) pair(s) not "
            f"present in traffic (e.g. {sample}) -- likely a policy bug (wrong "
            f"client set or a stale second)"
        )

    merged = merged.drop(columns="_merge")

    bad_actions = set(actions["action"].unique()) - set(ACTIONS)
    if bad_actions:
        raise ValueError(f"unknown action(s) in actions table: {sorted(bad_actions)}")

    unknown_classes = set(traffic["client_class"].unique()) - set(_COST_PER_REQUEST_BY_VALUE)
    if unknown_classes:
        raise ValueError(f"unknown client_class value(s) in traffic: {sorted(unknown_classes)}")

    cost_per_request = merged["client_class"].map(_COST_PER_REQUEST_BY_VALUE)

    admitted_requests = merged["requests"].where(merged["action"] == "ALLOW", 0)
    throttle_mask = merged["action"] == "THROTTLE"
    admitted_requests = admitted_requests.where(~throttle_mask, merged["requests"].clip(upper=1))

    merged["admitted_requests"] = admitted_requests.astype(int)
    merged["admitted_cost"] = (admitted_requests * cost_per_request).astype(int)
    return merged


def attack_prevention(admitted: pd.DataFrame) -> float:
    """Fraction of attacker cost successfully denied. 1.0 if no attacker
    cost was ever generated (vacuously nothing to prevent)."""
    attacker_rows = admitted[admitted["client_class"].isin(_ATTACKER_VALUES)]
    generated = attacker_rows["cost"].sum()
    if generated == 0:
        return 1.0
    admitted_cost = attacker_rows["admitted_cost"].sum()
    return _clip01(1 - admitted_cost / generated)


def legitimate_admission(admitted: pd.DataFrame) -> float:
    """Fraction of legitimate cost successfully admitted. 1.0 if no
    legitimate cost was ever generated."""
    legit_rows = admitted[admitted["client_class"].isin(_LEGIT_VALUES)]
    generated = legit_rows["cost"].sum()
    if generated == 0:
        return 1.0
    admitted_cost = legit_rows["admitted_cost"].sum()
    return _clip01(admitted_cost / generated)


def overload_free(admitted: pd.DataFrame, cap: int = CAPACITY_CAP) -> float:
    """Fraction of seconds where total admitted cost stayed at or under
    the capacity cap."""
    per_second = admitted.groupby("second")["admitted_cost"].sum()
    total_seconds = len(per_second)
    if total_seconds == 0:
        return 1.0
    seconds_under_cap = (per_second <= cap).sum()
    return _clip01(seconds_under_cap / total_seconds)


def legitimate_block_safety(admitted: pd.DataFrame) -> float:
    """Fraction of legitimate client-seconds NOT spent in BLOCK. Only
    BLOCK counts against this axis -- THROTTLE is free here by design
    (see the Scoring Model note's block/throttle asymmetry)."""
    legit_rows = admitted[admitted["client_class"].isin(_LEGIT_VALUES)]
    total = len(legit_rows)
    if total == 0:
        return 1.0
    blocked = (legit_rows["action"] == "BLOCK").sum()
    return _clip01(1 - blocked / total)


@dataclass(frozen=True)
class Score:
    attack_prevention: float
    legitimate_admission: float
    overload_free: float
    legitimate_block_safety: float

    @property
    def weighted_total(self) -> float:
        return (
            35 * self.attack_prevention
            + 30 * self.legitimate_admission
            + 20 * self.overload_free
            + 10 * self.legitimate_block_safety
        )

    def as_dict(self) -> dict[str, float]:
        return {
            "AttackPrevention": self.attack_prevention,
            "LegitimateAdmission": self.legitimate_admission,
            "OverloadFree": self.overload_free,
            "LegitimateBlockSafety": self.legitimate_block_safety,
            "WeightedTotal": self.weighted_total,
        }


def score(traffic: pd.DataFrame, actions: pd.DataFrame, cap: int = CAPACITY_CAP) -> Score:
    """Compute all four axes (plus the weighted total) for a given set of
    per-(second, client) actions against the given traffic."""
    admitted = apply_actions(traffic, actions)
    return Score(
        attack_prevention=attack_prevention(admitted),
        legitimate_admission=legitimate_admission(admitted),
        overload_free=overload_free(admitted, cap=cap),
        legitimate_block_safety=legitimate_block_safety(admitted),
    )
