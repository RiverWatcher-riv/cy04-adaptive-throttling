"""CY-04 live demo dashboard -- Phase 6.

Built to be watched, not read: the entire job is to make the two-signal
design visible in under 60 seconds, with a wow moment a naive rate
limiter provably cannot produce. Per the build guide's demo spec:

  1. Replay controls (play / scrub, adjustable speed)
  2. Capacity gauge (live admitted-cost-vs-220-cap)
  3. Color-coded client map, updating per second
  4. Running "attacker cost denied" counter, framed as cost not requests
  5. Ground-truth reveal toggle, hidden by default
  6. Final scorecard, revealed at the end of a run

Everything is precomputed once (the full run is deterministic and
cheap -- a few seconds), then "playback" is just slicing that frame by
`second`. That's what makes the two rehearsed demo moments below
reliably reproducible on every run, not dependent on RNG luck live.
"""

from __future__ import annotations

import time

import streamlit as st

from cy04.config import CAPACITY_CAP, DURATION_S, SEED, ClientClass
from cy04.metrics import apply_actions, score
from cy04.run_eval import run_policy
from cy04.simulator import client_roster, generate_traffic

ACTION_COLOR = {"ALLOW": "#2ecc71", "THROTTLE": "#f39c12", "BLOCK": "#e74c3c"}

# Rehearsed demo moments, pinned to the dev seed -- see the Parameter
# Register note. Burst windows come from the simulator/roster and are
# unaffected by any policy tuning, so these stay valid across phases.
BURSTY_SPOTLIGHT_CLIENT = 1
BURSTY_SPOTLIGHT_SECOND = 265  # ~4s before client 1's first burst starts at 269
LOW_AND_SLOW_SPOTLIGHT_SECOND = 100  # any settled second works; flagged from early on

st.set_page_config(page_title="CY-04 Adaptive Throttling — Live Demo", layout="wide")


@st.cache_data(show_spinner="Running the full 600s policy replay once...")
def load_run(seed: int):
    traffic = generate_traffic(seed)
    log = run_policy(traffic)
    admitted = apply_actions(traffic, log[["second", "client_id", "action"]])
    admitted["reason"] = log["reason"]
    roster = {c.client_id: c for c in client_roster(seed)}
    full_score = score(traffic, log[["second", "client_id", "action"]])
    return traffic, log, admitted, roster, full_score


traffic, log, admitted, roster, full_score = load_run(SEED)

if "second" not in st.session_state:
    st.session_state.second = 0
if "playing" not in st.session_state:
    st.session_state.playing = False

st.title("CY-04 — Adaptive API Abuse Throttling")
st.caption(
    "Online, causal, class-blind. The policy never sees the labels below "
    "until you reveal them -- watch it separate attackers from legitimate "
    "traffic using nothing but per-second request count and cost."
)

# --- Replay controls --------------------------------------------------------

ctrl = st.columns([1, 1, 1, 1, 3])
with ctrl[0]:
    if st.button("Pause" if st.session_state.playing else "Play", type="primary"):
        st.session_state.playing = not st.session_state.playing
with ctrl[1]:
    speed = st.selectbox("Speed", [1, 5, 20, 50], index=2)
with ctrl[2]:
    if st.button("Reset"):
        st.session_state.second, st.session_state.playing = 0, False
with ctrl[3]:
    if st.button("Jump: bursty-legit burst"):
        st.session_state.second, st.session_state.playing = BURSTY_SPOTLIGHT_SECOND, False
    if st.button("Jump: low-and-slow spotlight"):
        st.session_state.second, st.session_state.playing = LOW_AND_SLOW_SPOTLIGHT_SECOND, False
with ctrl[4]:
    st.session_state.second = st.slider("Second", 0, DURATION_S - 1, st.session_state.second)

reveal = st.toggle("Reveal ground-truth classes", value=False)

t = st.session_state.second
window = admitted[admitted["second"] <= t]
current = admitted[admitted["second"] == t]
latest = log[log["second"] == t].copy()

# --- Capacity gauge + attacker-cost-denied counter ---------------------------

top = st.columns(2)
with top[0]:
    admitted_now = int(current["admitted_cost"].sum())
    st.metric(f"Admitted cost this second (cap {CAPACITY_CAP})", admitted_now)
    st.progress(min(1.0, admitted_now / CAPACITY_CAP))
with top[1]:
    attacker_mask = window["client_class"].isin(
        [ClientClass.SUSTAINED_ATTACKER.value, ClientClass.LOW_AND_SLOW_ATTACKER.value]
    )
    denied = int(window.loc[attacker_mask, "cost"].sum() - window.loc[attacker_mask, "admitted_cost"].sum())
    st.metric("Attacker cost denied so far", f"{denied:,}")

# --- Live client map: rate vs. cost-per-request, colored by action ----------

st.subheader("Live client map — rate vs. cost-per-request")
st.caption(
    "x = request rate (EWMA), y = cost per request (EWMA). Legitimate traffic "
    "sits at y≈1 regardless of rate; the low-and-slow attacker cohort sits at "
    "y≈5 while its rate looks unremarkable -- a request-count limiter would "
    "never separate this axis."
)
plot_df = latest[["client_id", "rate_ewma_fast", "cost_ratio", "action"]].copy()
plot_df["color"] = plot_df["action"].map(ACTION_COLOR)
st.scatter_chart(plot_df, x="rate_ewma_fast", y="cost_ratio", color="color", size=60)

if reveal:
    st.subheader("Ground truth (hidden by default)")
    latest_with_class = latest.copy()
    breakdown = latest_with_class.groupby("client_class")["action"].value_counts().unstack(fill_value=0)
    st.dataframe(breakdown)

    ls = latest_with_class[latest_with_class["client_class"] == ClientClass.LOW_AND_SLOW_ATTACKER.value]
    if len(ls):
        flagged = int((ls["action"] != "ALLOW").sum())
        st.caption(
            f"Low-and-slow cohort right now: {flagged}/{len(ls)} flagged (THROTTLE or BLOCK). "
            f"Mean rate={ls['rate_ewma_fast'].mean():.2f} req/s (looks unremarkable) — "
            f"mean cost-per-request={ls['cost_ratio'].mean():.2f} (the tell)."
        )

# --- Final scorecard, revealed at the end of a run --------------------------

st.subheader("Scorecard")
if t >= DURATION_S - 1:
    d = full_score.as_dict()
    cols = st.columns(5)
    cols[0].metric("AttackPrevention (35)", f"{d['AttackPrevention']:.3f}")
    cols[1].metric("LegitimateAdmission (30)", f"{d['LegitimateAdmission']:.3f}")
    cols[2].metric("OverloadFree (20)", f"{d['OverloadFree']:.3f}")
    cols[3].metric("LegitimateBlockSafety (10)", f"{d['LegitimateBlockSafety']:.3f}")
    cols[4].metric("Weighted total", f"{d['WeightedTotal']:.2f} / 95")
else:
    st.info(f"Reach second {DURATION_S - 1} (or click Reset then Play) to reveal the final scorecard.")

# --- Autoplay ----------------------------------------------------------------

if st.session_state.playing:
    if st.session_state.second >= DURATION_S - 1:
        st.session_state.playing = False
    else:
        time.sleep(0.08)
        st.session_state.second = min(DURATION_S - 1, st.session_state.second + speed)
        st.rerun()
