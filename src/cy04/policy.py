"""Phase 3 -- the adaptive throttling policy.

Five layers, in the dependency order set out in the project's Policy
Design note. Each layer is a clearly separated section below; the
decision function at the bottom composes them.

  L1 Signals             -- dual EWMA rate (fast/slow) + cost, per client
  L2 Cost-ratio detector -- cost/rate ratio, the low-and-slow "signature"
  L3 Trust + persistence -- history-aware burst forgiveness
  L4 Escalation ladder   -- THROTTLE-first, BLOCK as a narrow compound case
  L5 Capacity guard       -- system-level backstop, independent of L1-L4

All state is per-client and updated causally: a decision computed from
signals observed through second t is applied starting at second t+1,
never at t (enforced by the causal loop in run_eval.py, not here).

Why BLOCK never fires on cost_ewma alone: a sustained attacker (rate=4,
cost/req=1) and a bursty-legit client mid-burst (rate=6, cost/req=1)
produce comparable *absolute* cost load -- cost_ewma cannot tell them
apart, because the difference between them is not price, it's
duration. So the "sustained, low-cost-ratio" BLOCK path (see
block_persistence_slow below) leans on duration instead: a legit burst
is capped at exactly BURST_DURATION_S (20s) by the simulator's own
spec, so setting that path's persistence bar safely above 20 makes a
legit burst mathematically unable to cross it, independent of trust
tuning. The cost dimension only gates the *other*, faster BLOCK path,
reserved for clients with an attacker-level cost-per-request signature
(low-and-slow) -- and no legit request ever costs more than 1, so that
path is closed to legit clients by construction, not by threshold luck.
"""

from __future__ import annotations

from dataclasses import dataclass

from cy04.config import BURST_COUNT, BURST_DURATION_S, CAPACITY_CAP

# Longest anomalous streak a legitimate client can physically produce: the
# simulator permits burst windows to be adjacent (non-overlapping, but
# back-to-back), so all BURST_COUNT bursts can run contiguously, plus the
# few seconds the fast EWMA takes to decay back under threshold afterward.
# Derived from config rather than hardcoded so the safety bound follows
# automatically if the burst spec changes.
_MAX_LEGIT_ANOMALY_STREAK = BURST_COUNT * BURST_DURATION_S + 6


@dataclass
class PolicyParams:
    """All tunable constants, named -- see the Parameter Register note.
    Phase 3 starting values, empirically checked against the real
    simulator (see scripts/phase3_report.py); Phase 4 tunes these
    against the scoring formulas."""

    # L1 -- EWMA half-lives (seconds)
    rate_halflife_fast: float = 3.0
    rate_halflife_slow: float = 60.0
    cost_halflife: float = 3.0

    # L2 -- cost-per-request ratio
    cost_ratio_threshold: float = 2.0

    # L1/L3 -- rate-anomaly detection
    absolute_rate_threshold: float = 1.8  # Phase 4 tuned (was 2.5): still comfortably above
    # low-and-slow (1.5) and all legit baselines, but catches the
    # sustained attacker (4.0) faster. +3.5% AttackPrevention,
    # -0.3% LegitimateAdmission on the tuning-loop seeds; re-verified
    # safe (zero legit BLOCKs, all attackers still escalate) across
    # 8 seeds before adopting -- this threshold feeds the same
    # rate_anomalous signal the block-safety guarantee depends on.
    relative_burst_multiple: float = 3.0  # fast > multiple * slow => relatively anomalous
    relative_floor: float = 1.5  # minimum-evidence gate on the relative check: a
    # "3x your own baseline" spike is only meaningful if
    # the absolute level is non-trivial too. Without this,
    # a client whose baseline is near zero trips the
    # relative check on ordinary noise.

    # L3 -- trust accumulator
    trust_gain_rate: float = 0.05
    trust_decay_rate: float = 0.03  # deliberately slow: the whole point of the trust
    # accumulator is that a long clean history survives a
    # burst. At 0.30/s a 20s burst wiped ~99.9% of accrued
    # trust, which defeated the mechanism it exists for.
    trust_gate_threshold: float = 0.5  # below this: fast-tracked for THROTTLE

    # L4 -- escalation ladder
    throttle_persistence: int = 1  # Phase 4 tuned (was 2): react to a rate anomaly one
    # second sooner. Small, safe gain -- verified no legit BLOCKs
    # introduced across 8 seeds.
    block_trust_ceiling: float = 0.2
    block_persistence_fast: int = 4  # Phase 4 tuned (was 8): the cost-ratio-flagged path is
    # already near-zero-false-positive (Layer 2 is structural,
    # not statistical), so there's little risk in shortening its
    # bar to cut low-and-slow's pre-BLOCK admission window.
    block_persistence_slow: int = _MAX_LEGIT_ANOMALY_STREAK + 24  # = 90; duration-only BLOCK path.
    # Deliberately set above the longest anomalous streak a legit client can
    # physically produce (see _MAX_LEGIT_ANOMALY_STREAK), so this path is
    # unreachable for legit traffic by construction rather than by tuning.
    # Costs ~3% AttackPrevention vs. a bar of 30 -- measured, and accepted:
    # it converts a coincidence into a guarantee.
    block_clean_history_max: int = 15  # a client with >= this many CONSECUTIVE clean seconds
    # right now can never be BLOCKed. Must be consecutive, not
    # cumulative: the spec places every burst start at second
    # >= 20, so every bursty-legit client banks >=20 consecutive
    # clean seconds immediately before its first burst, and
    # normal-legit banks long consecutive runs throughout.
    # Attackers are anomalous persistently enough that a 15-
    # second unbroken clean streak essentially never occurs (a
    # cumulative counter was tried first and failed: it let a
    # sustained attacker drift past the bar from scattered
    # clean-looking seconds caused by Poisson noise, granting
    # permanent BLOCK immunity to specific attacker clients on
    # 3 of 5 tested seeds). Relying on trust-decay tuning alone
    # was also tried and also failed -- adjacent burst windows
    # produce anomalous streaks of 40s+, long enough to decay
    # accrued trust below the ceiling on an unlucky seed.

    # L5 -- capacity guard
    capacity_soft_cap: float = 0.9 * CAPACITY_CAP  # trigger margin below the hard cap


def _ewma_alpha(halflife: float) -> float:
    return 1.0 - 0.5 ** (1.0 / halflife)


@dataclass
class ClientState:
    """Per-client rolling state, updated once per second."""

    rate_ewma_fast: float = 0.0
    rate_ewma_slow: float = 0.0
    cost_ewma: float = 0.0
    trust: float = 0.0
    persistence_counter: int = 0
    consecutive_clean_seconds: int = 0  # resets on any anomalous second -- gates BLOCK
    # eligibility. Must be a streak, not a lifetime tally: a
    # cumulative counter lets a sustained attacker drift past
    # the immunity bar from scattered clean-looking seconds
    # caused by ordinary Poisson noise, silently making that
    # specific attacker permanently BLOCK-immune. A consecutive
    # streak requires sustained good behavior, which is what
    # the guarantee is actually supposed to mean.
    cost_ratio: float = 0.0
    cost_ratio_flagged: bool = False
    rate_anomalous: bool = False
    action: str = "ALLOW"  # action currently in effect (decided from data through t-1)
    reason: str = "cold_start_default"  # why `action` was chosen -- see decide_action().
    # Phase 6 requirement: the action log needs a reason code per
    # decision, not just the raw action, or the error analysis is
    # hand-wavy instead of specific.
    warmed_up: bool = False  # see update_signals: both EWMAs are seeded from the
    # first observation rather than converging up from zero


def new_state() -> ClientState:
    return ClientState()


# --- L1: signals -------------------------------------------------------


def update_signals(state: ClientState, requests: int, cost: int, params: PolicyParams) -> None:
    """Update the dual rate EWMAs and the cost EWMA from this second's
    raw observation (requests, cost) -- the controller watches every
    arriving request, regardless of what action was applied to it.

    Both EWMAs are warm-started from the first observation. Starting
    them at zero instead is a real trap: the slow EWMA (60s half-life)
    climbs toward the true rate far more slowly than the fast one (3s),
    so for the first minute-plus of a run EVERY client -- legitimate
    ones included -- looks like it is spiking relative to its own
    baseline, purely because that baseline hasn't caught up yet.
    """
    if not state.warmed_up:
        state.rate_ewma_fast = float(requests)
        state.rate_ewma_slow = float(requests)
        state.cost_ewma = float(cost)
        state.warmed_up = True
        return

    af = _ewma_alpha(params.rate_halflife_fast)
    a_slow = _ewma_alpha(params.rate_halflife_slow)
    ac = _ewma_alpha(params.cost_halflife)

    state.rate_ewma_fast += af * (requests - state.rate_ewma_fast)
    state.rate_ewma_slow += a_slow * (requests - state.rate_ewma_slow)
    state.cost_ewma += ac * (cost - state.cost_ewma)


# --- L2: cost-per-request ratio -----------------------------------------


def update_cost_ratio(state: ClientState, params: PolicyParams) -> None:
    if state.rate_ewma_fast < 1e-6:
        state.cost_ratio = 0.0
    else:
        state.cost_ratio = state.cost_ewma / state.rate_ewma_fast
    state.cost_ratio_flagged = state.cost_ratio > params.cost_ratio_threshold


def update_rate_anomaly(state: ClientState, params: PolicyParams) -> None:
    """Rate anomaly if EITHER absolute (separates the sustained
    attacker, whose own baseline eventually rises to meet a purely
    relative check) OR relative to the client's own slow baseline
    (separates a legit burst from that client's own quiet history)."""
    absolute = state.rate_ewma_fast > params.absolute_rate_threshold
    relative_bar = max(
        params.relative_burst_multiple * state.rate_ewma_slow, params.relative_floor
    )
    relative = state.rate_ewma_fast > relative_bar
    state.rate_anomalous = absolute or relative


# --- L3: trust + persistence ---------------------------------------------


def update_trust_and_persistence(state: ClientState, params: PolicyParams) -> None:
    clean = (not state.cost_ratio_flagged) and (not state.rate_anomalous)
    if clean:
        state.trust += params.trust_gain_rate * (1.0 - state.trust)
        state.persistence_counter = 0
        state.consecutive_clean_seconds += 1
    else:
        state.trust *= 1.0 - params.trust_decay_rate
        state.persistence_counter += 1
        state.consecutive_clean_seconds = 0


# --- L4: escalation ladder -------------------------------------------------


def decide_action(state: ClientState, params: PolicyParams, max_layer: int = 5) -> tuple[str, str]:
    """The (action, reason_code) to apply starting next second, from
    signals observed through this second. Stateless given current
    signals -- this is what gives decay-back to ALLOW "for free": once
    signals normalize, neither escalation condition holds and the
    decision reverts on its own, with no separate cooldown state to
    manage.

    The reason code is what turns the action log into evidence instead
    of a bare trace -- it's what a per-decision "why" in the demo and
    error analysis actually points at.

    `max_layer` gates how much of the ladder is active, so the build
    guide's step-by-step incremental scoring (Phase 3, steps 1-5) can
    be run against genuine partial configurations instead of being
    reconstructed after the fact from the finished policy:

      1 -- signals only, no decision logic at all (always ALLOW)
      2 -- cost-ratio detector only (the L2 headline feature, isolated)
      3 -- + trust/persistence-gated rate anomaly (THROTTLE only, no BLOCK)
      4 -- + the full escalation ladder, BLOCK included (this function's
           complete logic)
      5 -- L4 unchanged; the capacity guard is a separate system-level
           pass applied in run_eval.py, not part of this function
    """
    if max_layer < 2:
        return "ALLOW", "layer1_signals_only"

    if max_layer == 2:
        if state.cost_ratio_flagged:
            return "THROTTLE", "cost_ratio_flagged"
        return "ALLOW", "clean"

    rate_escalate = (
        state.persistence_counter >= params.throttle_persistence
        and state.trust < params.trust_gate_threshold
    )
    throttle_eligible = state.cost_ratio_flagged or rate_escalate
    throttle_reason = "cost_ratio_flagged" if state.cost_ratio_flagged else "sustained_rate_anomaly_low_trust"

    if max_layer == 3:
        return ("THROTTLE", throttle_reason) if throttle_eligible else ("ALLOW", "clean")

    never_established_history = state.consecutive_clean_seconds < params.block_clean_history_max
    fast_block = (
        never_established_history
        and state.trust < params.block_trust_ceiling
        and state.cost_ratio_flagged
        and state.persistence_counter >= params.block_persistence_fast
    )
    slow_block = (
        never_established_history
        and state.trust < params.block_trust_ceiling
        and state.persistence_counter >= params.block_persistence_slow
    )
    if fast_block:
        return "BLOCK", "block_fast_path_cost_ratio_signature"
    if slow_block:
        return "BLOCK", "block_slow_path_sustained_duration"

    if throttle_eligible:
        return "THROTTLE", throttle_reason

    return "ALLOW", "clean"


def step_client(
    state: ClientState, requests: int, cost: int, params: PolicyParams, max_layer: int = 5
) -> None:
    """Advance one client's signal/decision state by one second's
    observation. `state.action` afterward is the decision for NEXT
    second -- the caller applies the PRE-step `state.action` value to
    THIS second's admission before calling this. See run_eval.py.

    Signals (L1/L2/L3) are always updated in full regardless of
    `max_layer` -- only the decision at the end is gated. This matches
    Phase 3 step 1's intent: "log signals, make no decision yet" is a
    property of the ACTION, not of whether the signals themselves exist.
    """
    update_signals(state, requests, cost, params)
    update_cost_ratio(state, params)
    update_rate_anomaly(state, params)
    update_trust_and_persistence(state, params)
    state.action, state.reason = decide_action(state, params, max_layer=max_layer)


# --- L5: capacity guard (system-level, spans all clients) ------------------


def apply_capacity_guard(
    provisional_actions: dict[int, str],
    states: dict[int, "ClientState"],
    params: PolicyParams,
) -> dict[int, str]:
    """Project next-second admitted cost per client from CURRENT EWMA
    state (never from next second's actual draw -- causal by
    construction) under each client's provisionally-decided action. If
    the projected total exceeds the soft cap, downgrade the highest
    cost-signal / lowest-trust clients (ALLOW->THROTTLE->BLOCK) one
    step at a time until back under the cap or out of candidates.

    A rare backstop by design: if this fires often, per-client
    thresholds in L1-L4 are too permissive, not this layer too weak.

    Respects the same block-safety guarantee as decide_action(): a
    client with consecutive_clean_seconds >= block_clean_history_max can
    never be escalated to BLOCK here either. Without this check, a
    capacity-starved second could push a THROTTLEd, history-protected
    (e.g. legitimate, mid-burst) client straight to BLOCK on cost/trust
    ranking alone -- a purely capacity-driven decision with no
    persistence or trust requirement at all, silently reopening the
    exact hole L4 was built to close. Such a client is skipped and the
    guard looks past it to the next candidate instead.
    """

    def projected_cost(client_id: int, action: str) -> float:
        s = states[client_id]
        if action == "ALLOW":
            return s.cost_ewma
        if action == "THROTTLE":
            return s.cost_ratio if s.rate_ewma_fast > 1e-6 else 0.0
        return 0.0

    actions = dict(provisional_actions)
    total = sum(projected_cost(cid, a) for cid, a in actions.items())
    if total <= params.capacity_soft_cap:
        return actions

    downgrade_order = sorted(
        (cid for cid, a in actions.items() if a != "BLOCK"),
        key=lambda cid: (-states[cid].cost_ewma, states[cid].trust),
    )

    for cid in downgrade_order:
        if total <= params.capacity_soft_cap:
            break
        old_action = actions[cid]
        if old_action == "ALLOW":
            new_action = "THROTTLE"
        else:
            if states[cid].consecutive_clean_seconds >= params.block_clean_history_max:
                continue  # history-protected: worst case is THROTTLE, try the next candidate
            new_action = "BLOCK"
        total += projected_cost(cid, new_action) - projected_cost(cid, old_action)
        actions[cid] = new_action
        states[cid].reason = "capacity_guard_override"

    return actions
