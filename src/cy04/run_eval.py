"""Phase 3+ -- the causal evaluation loop.

Ties simulator traffic, the policy, and the scoring harness together
under one hard rule:

    a decision computed from data through second t applies to admission
    starting at second t+1, never at t.

This module is the only place that rule is enforced -- simulator.py
produces traffic without knowing about it, and metrics.py scores an
action table without knowing about it. The sequencing below is
therefore the load-bearing part of this file:

    for each second t:
      1. apply the action ALREADY held in state (decided through t-1)
         to second t's arrivals, and log it
      2. observe second t's arrivals, update signals/trust/persistence,
         and compute each client's provisional next action
      3. run the capacity guard over those provisional actions using
         only EWMA state observed through t

Step 1 strictly precedes step 2, so no decision can ever be informed by
the traffic it acts upon.
"""

from __future__ import annotations

import pandas as pd

from cy04.config import SEED
from cy04.metrics import Score, score
from cy04.policy import ClientState, PolicyParams, apply_capacity_guard, new_state, step_client
from cy04.simulator import generate_traffic


def run_policy(
    traffic: pd.DataFrame, params: PolicyParams | None = None, max_layer: int = 5
) -> pd.DataFrame:
    """Run the causal policy loop over `traffic`.

    Returns an action log: one row per (second, client_id) giving the
    action APPLIED during that second, alongside the signal values that
    were in effect when it was decided -- the per-decision "why" that
    Phase 6's error analysis and demo depend on.

    `max_layer` (1-5) runs a genuine partial configuration rather than
    the full policy -- see decide_action()'s docstring for what each
    level activates. Layer 5 (the capacity guard) is a separate,
    system-level pass below and is skipped entirely for max_layer < 5,
    exactly as the build guide's Phase 3 step-by-step scoring requires.
    """
    params = params or PolicyParams()

    client_ids = traffic["client_id"].unique()
    states: dict[int, ClientState] = {int(cid): new_state() for cid in client_ids}

    by_second = dict(tuple(traffic.groupby("second")))
    total_seconds = int(traffic["second"].max()) + 1

    rows = []
    for t in range(total_seconds):
        sec_df = by_second[t]

        # 1. Apply the action already in effect, decided through t-1.
        for row in sec_df.itertuples(index=False):
            s = states[row.client_id]
            rows.append(
                {
                    "second": t,
                    "client_id": row.client_id,
                    "client_class": row.client_class,
                    "requests": row.requests,
                    "cost": row.cost,
                    "action": s.action,
                    "rate_ewma_fast": s.rate_ewma_fast,
                    "rate_ewma_slow": s.rate_ewma_slow,
                    "cost_ewma": s.cost_ewma,
                    "cost_ratio": s.cost_ratio,
                    "cost_ratio_flagged": s.cost_ratio_flagged,
                    "rate_anomalous": s.rate_anomalous,
                    "trust": s.trust,
                    "persistence_counter": s.persistence_counter,
                }
            )

        # 2. Observe second t, update signals, decide provisional next action.
        for row in sec_df.itertuples(index=False):
            step_client(states[row.client_id], row.requests, row.cost, params, max_layer=max_layer)

        # 3. Capacity guard over the provisional next actions (layer 5 only).
        if max_layer >= 5:
            provisional = {cid: st.action for cid, st in states.items()}
            final_next = apply_capacity_guard(provisional, states, params)
            for cid, action in final_next.items():
                states[cid].action = action

    return pd.DataFrame(rows)


def evaluate(
    seed: int = SEED, params: PolicyParams | None = None
) -> tuple[pd.DataFrame, Score]:
    """Generate traffic for `seed`, run the policy, and score it."""
    traffic = generate_traffic(seed)
    action_log = run_policy(traffic, params)
    s = score(traffic, action_log[["second", "client_id", "action"]])
    return action_log, s


if __name__ == "__main__":
    _, result = evaluate()
    for key, value in result.as_dict().items():
        print(f"{key:24s} {value:.4f}")
