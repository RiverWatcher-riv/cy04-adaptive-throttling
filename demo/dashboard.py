"""CY-04 live demo dashboard.

Phase 1 placeholder: shows the traffic-generation sanity report so the
local-hosting pipeline is proven end-to-end from day one. Grows into the
full live-replay dashboard -- per-second admitted-cost gauge, color-coded
ALLOW/THROTTLE/BLOCK feed, running attacker-cost-denied counter, and a
ground-truth reveal toggle -- in Phase 6.
"""

import streamlit as st

from cy04.config import CAPACITY_CAP, SEED
from cy04.simulator import generate_traffic

st.set_page_config(page_title="CY-04 Adaptive Throttling", layout="wide")
st.title("CY-04 — Adaptive API Abuse Throttling")
st.caption(
    "Phase 1 status: traffic simulator only. "
    "The live policy replay dashboard lands in Phase 6."
)

df = generate_traffic(SEED)

st.subheader("Per-class traffic totals (raw — no policy applied yet)")
totals = df.groupby("client_class").agg(
    clients=("client_id", "nunique"),
    total_requests=("requests", "sum"),
    total_cost=("cost", "sum"),
)
totals["avg_cost_per_request"] = (totals["total_cost"] / totals["total_requests"]).round(2)
st.dataframe(totals)

st.subheader(f"Aggregate admitted cost per second (raw, cap = {CAPACITY_CAP})")
per_second = df.groupby("second")["cost"].sum()
st.line_chart(per_second)

over_cap = int((per_second > CAPACITY_CAP).sum())
st.metric("Seconds over cap (informational only — no policy yet)", over_cap)
