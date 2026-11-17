"""
Immaculate Vibes – Retention Action Engine (owner / staff app)

Run locally:   streamlit run app.py
Every Monday the owner (or the sales champion) uploads the latest sales export
and gets: the KPI check against targets, this week's list of who to message
(with the hold-out group marked), a win-back ROI calculator, and the data-quality
report for the file.
"""
from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from iv_retention import pipeline as p

BRAND = "#6A2C91"
SAMPLE = Path(__file__).parent / "sample_data" / "sample_sales.csv"

st.set_page_config(page_title="Immaculate Vibes – Retention", page_icon="🧵", layout="wide")


# ---------------------------------------------------------------------------
# Optional password gate (set APP_PASSWORD in Streamlit secrets to enable)
# ---------------------------------------------------------------------------
def _check_password() -> bool:
    try:
        pw = st.secrets.get("APP_PASSWORD")
    except Exception:  # no secrets file locally
        pw = None
    if not pw:
        return True
    if st.session_state.get("auth_ok"):
        return True
    entered = st.text_input("Password", type="password")
    if entered and entered == pw:
        st.session_state["auth_ok"] = True
        st.rerun()
    elif entered:
        st.error("Wrong password")
    return False


if not _check_password():
    st.stop()


@st.cache_data(show_spinner=False)
def _load(raw: bytes) -> pd.DataFrame:
    return p.load_orders(raw)


@st.cache_data(show_spinner=False)
def _run(raw: bytes, snapshot, drift, lapse, holdout, consent_raw):
    df = _load(raw)
    cfg = p.make_config(snapshot_date=snapshot, drift_days=drift, lapse_days=lapse, holdout_share=holdout)
    consent = pd.read_csv(io.BytesIO(consent_raw)) if consent_raw else None
    return p.run(df, cfg, consent)


def kes(x: float) -> str:
    return f"KES {x:,.0f}"


# ---------------------------------------------------------------------------
# Sidebar – inputs
# ---------------------------------------------------------------------------
st.sidebar.title("Immaculate Vibes")
st.sidebar.caption("Retention Action Engine")
up = st.sidebar.file_uploader("1. Upload the latest sales export (CSV)", type="csv")
if up is not None:
    raw = up.getvalue()
    src_label = up.name
elif SAMPLE.exists():
    raw = SAMPLE.read_bytes()
    src_label = "SAMPLE data (de-identified demo)"
else:
    st.info("Upload the sales export to begin.")
    st.stop()

consent_up = st.sidebar.file_uploader("2. Optional: consent register (customer_id, whatsapp_consent)", type="csv")

st.sidebar.markdown("**3. Settings**")
snap_mode = st.sidebar.radio("Score as of", ["Day after last order in file", "Today", "Pick a date"], index=0)
snapshot = None
if snap_mode == "Today":
    snapshot = date.today().isoformat()
elif snap_mode == "Pick a date":
    snapshot = st.sidebar.date_input("Date").isoformat()

auto = st.sidebar.checkbox("Set drift/lapse thresholds from the data (recommended)", value=True)
drift = lapse = None
if not auto:
    drift = st.sidebar.number_input("Drifting after (days silent)", 30, 720, 209)
    lapse = st.sidebar.number_input("Lapsed after (days silent)", 60, 900, 351)
holdout = st.sidebar.slider("Hold-out group (not messaged)", 0, 40, 20, 5,
                            help="A random share kept un-messaged so we can measure whether messages work.") / 100

try:
    res = _run(raw, snapshot, drift, lapse, holdout, consent_up.getvalue() if consent_up else None)
except p.DataValidationError as e:
    st.error(f"This file can't be used yet: {e}")
    st.stop()

k, t = res.kpis, res.config["targets"]
st.title("Retention Action Engine")
st.caption(f"Source: {src_label} · scored as of **{k['snapshot_date']}** · "
           f"{k['customers']:,} customers · {k['orders']:,} orders")
if "SAMPLE" in src_label:
    st.warning("You are looking at the de-identified sample. Upload the real export in the sidebar.")

tab_kpi, tab_act, tab_seg, tab_roi, tab_mon, tab_dq = st.tabs(
    ["📊 KPIs", "📲 This week's actions", "👥 Segments", "💰 Win-back calculator", "📈 Did it work?", "✅ Data quality"])

# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------
with tab_kpi:
    st.subheader("Are we on track?")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("2nd order within 90 days (primary KPI)", f"{k['second_order_90d']:.0%}",
              f"target {t['second_order_90d']:.0%}", delta_color="off",
              help=f"Customers whose first order was ≥90 days ago (n={k['second_order_90d_n']}). "
                   f"Recent cohort (first order 90–180 days ago): {k['second_order_90d_recent']:.0%} "
                   f"(n={k['second_order_90d_recent_n']}).")
    c2.metric("Cancelled + returned orders", f"{k['cancel_return_rate']:.1%}",
              f"last 90 days {k['cancel_return_rate_last90']:.1%} · target {t['cancel_return_rate']:.1%}",
              delta_color="off")
    c3.metric("Regulars drifting / lapsed", f"{k['drifting']} / {k['lapsed']}",
              f"{kes(k['winback_pool_revenue'])} past revenue", delta_color="off")
    c4.metric("Gross margin (estimate)", f"{k['gross_margin']:.0%}", f"hold ≥ {t['gross_margin']:.0%}",
              delta_color="off")
    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Repeat customers", f"{k['repeat_rate']:.0%}")
    c6.metric("New customers / month (last 3 full months)", f"{k['new_customers_per_month_last3']:.0f}")
    c7.metric("First-timers in nudge window (0–90 days)", k["new_in_nudge_window"])
    c8.metric("Delivered revenue (all time)", kes(k["delivered_revenue"]))
    st.caption(f"Drifting = silent longer than {res.drift_days:.0f} days (75% of repeat gaps are shorter); "
               f"Lapsed = silent longer than {res.lapse_days:.0f} days (90%). "
               "Small recent cohorts move a lot week to week — judge the trend over a month, not one week.")

# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
with tab_act:
    a = res.actions
    to_msg = a[a["group"] == "Message"]
    st.subheader(f"{len(to_msg)} customers to contact this week")
    st.markdown(
        "**How to use:** work top to bottom (most valuable first). Message on WhatsApp **only if consent is "
        "'Yes'** — if it says *Unknown*, ask the customer first or skip. **Do not message the hold-out "
        "rows**: they are how we prove the messages work. Tick off each customer in your own copy.")
    f1, f2 = st.columns([2, 1])
    kinds = f1.multiselect("Show", sorted(a["action"].unique()), default=sorted(a["action"].unique()))
    show_hold = f2.checkbox("Show hold-out rows", value=False)
    view = a[a["action"].isin(kinds)]
    if not show_hold:
        view = view[view["group"] == "Message"]
    st.dataframe(view, width="stretch", hide_index=True,
                 column_config={"lifetime_revenue": st.column_config.NumberColumn("lifetime revenue (KES)",
                                                                                  format="%d"),
                                "suggested_message": st.column_config.TextColumn(width="large")})
    st.download_button("⬇️ Download full action list (incl. hold-out)", a.to_csv(index=False).encode(),
                       f"action_list_{k['snapshot_date']}.csv", "text/csv")
    with st.expander("Message guide for staff"):
        for step, msg in p.NUDGE_MESSAGES.items():
            st.markdown(f"- **{msg.split(' – ')[0]}** – {msg.split(' – ')[1].format(product='their last outfit')}")
        for s_, msg in p.WINBACK_MESSAGES.items():
            st.markdown(f"- **Win-back ({s_.lower()})** – {msg}")
        st.markdown("- Always include an opt-out line: *Reply STOP and we won't message you again.* "
                    "Record any STOP in the consent register the same day.")

# ---------------------------------------------------------------------------
# Segments
# ---------------------------------------------------------------------------
with tab_seg:
    seg = res.segments.reset_index().rename(columns={"index": "Segment"})
    st.subheader("Who our customers are (RFM segments)")
    l, r = st.columns(2)
    for col, field, title, fmt in [(l, "customers", "Customers per segment", ",.0f"),
                                   (r, "revenue_share", "Share of delivered revenue", ".0%")]:
        ch = (alt.Chart(seg).mark_bar(color=BRAND, cornerRadiusEnd=4)
              .encode(y=alt.Y("Segment:N", sort=p.SEG_ORDER, title=None),
                      x=alt.X(f"{field}:Q", title=None, axis=alt.Axis(format=fmt, grid=True)),
                      tooltip=["Segment", alt.Tooltip(f"{field}:Q", format=fmt)])
              .properties(title=title, height=240))
        col.altair_chart(ch, width="stretch")
    st.dataframe(seg.style.format({"customers": "{:,.0f}", "avg_recency_days": "{:,.0f}", "avg_orders": "{:.1f}",
                                   "avg_revenue": "{:,.0f}", "total_revenue": "{:,.0f}",
                                   "customer_share": "{:.0%}", "revenue_share": "{:.0%}"}),
                 width="stretch", hide_index=True)
    cs = res.lifecycle[["status", "Segment", "delivered_orders", "total_revenue", "days_since_last_order",
                        "first_channel", "county", "age_group"]]
    st.download_button("⬇️ Download every customer's status", cs.to_csv().encode(),
                       f"customer_status_{k['snapshot_date']}.csv", "text/csv")

# ---------------------------------------------------------------------------
# ROI calculator
# ---------------------------------------------------------------------------
with tab_roi:
    st.subheader("Is a win-back campaign worth it?")
    st.caption("A forecast, not a result: change the assumptions and see when the campaign pays for itself.")
    n_pool = int(((res.actions["action"].str.startswith("Win-back")) & (res.actions["group"] == "Message")).sum())
    aov_default = float(res.rfm["Monetary"].sum() / max(res.rfm["Frequency"].sum(), 1))
    a1, a2, a3 = st.columns(3)
    contacted = a1.number_input("Customers messaged", 0, 5000, n_pool)
    rr = a1.slider("Extra customers who come back because of the message (%)", 0, 40, 10) / 100
    opw = a2.number_input("Orders per won-back customer in the year", 1.0, 6.0, 2.0, 0.5)
    aov = a2.number_input("Average order value (KES)", 500, 20000, int(round(aov_default, -1)), 50)
    margin = a3.slider("Gross margin (%)", 20, 70, int(round((k["gross_margin"] or 0.49) * 100))) / 100
    cpc = a3.number_input("Cost per message incl. perk (KES)", 0, 2000, 150, 10)
    fixed = a3.number_input("Fixed cost (staff time, design) KES", 0, 500000, 5000, 1000)
    r_ = p.roi_scenario(contacted, rr, opw, aov, margin, cpc, fixed)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Customers won back", f"{r_['customers_won']:.0f}")
    m2.metric("Extra revenue", kes(r_["extra_revenue"]))
    m3.metric("Extra gross profit", kes(r_["extra_gross_profit"]))
    m4.metric("Net benefit (profit – cost)", kes(r_["net_benefit"]))
    st.info(f"Break-even: the campaign pays for itself if at least **{r_['break_even_response_rate']:.1%}** "
            f"of messaged customers come back *because of* the message "
            f"(≈ {r_['break_even_response_rate'] * contacted:.1f} customers).")
    st.markdown("**Sensitivity – net benefit (KES) by uplift and margin**")
    grid = p.sensitivity_grid(contacted, aov, cpc, fixed, [0.02, 0.05, 0.10, 0.15, 0.20],
                              [0.40, 0.45, 0.49, 0.55], opw)
    grid.columns = [f"{c:.0%} uplift" for c in grid.columns]
    grid.index = [f"{i:.0%} margin" for i in grid.index]
    st.dataframe(grid.style.format("{:,.0f}"), width="stretch")
    st.caption("Uplift = return rate of messaged group minus return rate of hold-out group. About half of "
               "flagged regulars came back within 6 months *without* any message in the back-test, so only "
               "the difference counts.")

# ---------------------------------------------------------------------------
# Monitoring
# ---------------------------------------------------------------------------
with tab_mon:
    st.subheader("Did last campaign work?")
    st.markdown("Upload an **earlier action list** (downloaded from this app). We check who has ordered since, "
                "for messaged vs hold-out customers.")
    prev = st.file_uploader("Earlier action list CSV", type="csv", key="prev")
    since = st.date_input("Campaign start date", key="since")
    if prev is not None:
        pl = pd.read_csv(prev)
        conv = p.winback_conversion(_load(raw), pl, since)
        st.dataframe(conv.style.format({"rate": "{:.0%}"}), width="stretch", hide_index=True)
        if "group" in conv.columns and conv["group"].nunique() == 2:
            for at, g in conv.groupby("action_type"):
                g = g.set_index("group")["rate"]
                if len(g) == 2:
                    up_ = g.get("Message", float("nan")) - g.get("Hold-out (do NOT message)", float("nan"))
                    st.markdown(f"**{at}**: uplift = {up_:+.0%} points (messaged minus hold-out).")
            st.caption("With groups this small, treat a difference under ~10 points as 'not proven yet'. "
                       "Keep running for 2–3 months before deciding (report decision gate at month 6).")

# ---------------------------------------------------------------------------
# Data quality
# ---------------------------------------------------------------------------
with tab_dq:
    q = res.quality
    st.subheader("Is this export safe to use?")
    if q["ok"]:
        st.success("No blocking problems – scoring ran.")
    else:
        st.error("Blocking problems found.")
    st.write({k_: q[k_] for k_ in ["rows", "customers", "first_order", "last_order", "sequence_match_rate",
                                   "status_counts"]})
    for w in q["warnings"]:
        st.warning(w)
    if not q["warnings"]:
        st.info("No warnings.")
