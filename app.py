"""
Immaculate Vibes 254 – Retention Action Engine (owner / staff app)

Run locally:   streamlit run app.py
Every Monday the owner (or the sales champion) uploads the latest sales export
and gets: the KPI check against targets, this week's list of who to message
(with the hold-out group marked), a win-back ROI calculator, and the data-quality
report for the file.
"""
from __future__ import annotations

import io
import os
from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from iv_retention import pipeline as p

from iv_retention import ui

BRAND = ui.PLUM
SAMPLE = Path(__file__).parent / "sample_data" / "sample_sales.csv"

st.set_page_config(page_title="Immaculate Vibes 254 – Retention", layout="wide",
                   page_icon=str(ui.icon_path()) if ui.icon_path() else "🧵")
ui.inject_css()


# ---------------------------------------------------------------------------
# Password gate (set APP_PASSWORD in Streamlit secrets to enable)
# ---------------------------------------------------------------------------
def _check_password() -> bool:
    # Render / Docker: environment variable. Streamlit Cloud: secrets.toml.
    pw = os.environ.get("APP_PASSWORD")
    if not pw:
        try:
            pw = st.secrets.get("APP_PASSWORD")
        except Exception:  # no secrets file locally
            pw = None
    if not pw and os.environ.get("RENDER"):
        # Fail closed on a public host: never serve the app without a password
        st.error("This app is not configured yet: set APP_PASSWORD in Render > Environment.")
        return False
    if not pw or st.session_state.get("auth_ok"):
        return True
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        st.markdown(f'<div style="text-align:center;margin:12vh 0 18px">{ui.logo_html(84)}</div>',
                    unsafe_allow_html=True)
        st.markdown('<h3 style="text-align:center;margin-bottom:4px">Retention Action Engine</h3>'
                    '<p style="text-align:center;color:#6B6275">Staff access only</p>', unsafe_allow_html=True)
        with st.form("login"):
            entered = st.text_input("Password", type="password")
            ok = st.form_submit_button("Sign in", width="stretch", type="primary")
        if ok and entered == pw:
            st.session_state["auth_ok"] = True
            st.rerun()
        elif ok:
            st.error("That password isn't right.")
    return False


if not _check_password():
    st.stop()


@st.cache_data(show_spinner=False)
def _load(raw: bytes) -> pd.DataFrame:
    return p.load_orders(raw)


@st.cache_data(show_spinner="Scoring customers…")
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
ui.sidebar_logo()
st.sidebar.markdown("**Retention Action Engine**")
ui.side_step("Step 1 · This week's data")
up = st.sidebar.file_uploader("Latest sales export (CSV)", type="csv")
if up is not None:
    raw = up.getvalue()
    src_label = up.name
elif SAMPLE.exists():
    raw = SAMPLE.read_bytes()
    src_label = "SAMPLE data (de-identified demo)"
else:
    st.info("Upload the sales export to begin.")
    st.stop()

ui.side_step("Step 2 · Consent (optional)")
consent_up = st.sidebar.file_uploader("Consent register (customer_id, whatsapp_consent)", type="csv")

ui.side_step("Step 3 · Settings")
snap_mode = st.sidebar.radio("Score as of", ["Day after last order in file", "Today", "Pick a date"], index=0)
snapshot = None
if snap_mode == "Today":
    snapshot = date.today().isoformat()
elif snap_mode == "Pick a date":
    snapshot = st.sidebar.date_input("Date").isoformat()

with st.sidebar.expander("Advanced"):
    auto = st.checkbox("Set drift/lapse thresholds from the data (recommended)", value=True)
    drift = lapse = None
    if not auto:
        drift = st.number_input("Drifting after (days silent)", 30, 720, 209)
        lapse = st.number_input("Lapsed after (days silent)", 60, 900, 351)
    holdout = st.slider("Hold-out group (not messaged) %", 0, 40, 20, 5,
                        help="A random share kept un-messaged so we can measure whether messages work.") / 100

try:
    res = _run(raw, snapshot, drift, lapse, holdout, consent_up.getvalue() if consent_up else None)
except p.DataValidationError as e:
    st.error(f"This file can't be used yet: {e}")
    st.stop()

k, t = res.kpis, res.config["targets"]
n_msg = int((res.actions["group"] == "Message").sum()) if len(res.actions) else 0
ui.header("Retention Action Engine",
          f"Scored as of <b>{k['snapshot_date']}</b> · {k['customers']:,} customers · {k['orders']:,} orders")
is_sample = "SAMPLE" in src_label
ui.chips([("Source", src_label), ("To contact this week", f"{n_msg}"),
          ("Drift / lapse after", f"{res.drift_days:.0f} / {res.lapse_days:.0f} days"),
          ("Data check", "passed" if res.quality["ok"] else "failed")],
         warn={0} if is_sample else set())
if is_sample:
    st.warning("You're viewing the de-identified sample. Upload the real export in the sidebar (Step 1).")

tab_kpi, tab_act, tab_seg, tab_cust, tab_roi, tab_mon, tab_dq = st.tabs(
    ["Overview", f"This week's actions ({n_msg})", "Segments", "Customer lookup", "Win-back calculator",
     "Did it work?", "Data quality"])
orders = _load(raw)
orders = orders[orders["order_datetime"] < res.snapshot]

# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------
with tab_kpi:
    st.subheader("Are we on track?")
    c1, c2, c3, c4 = st.columns(4)
    so, so_t = k["second_order_90d"], t["second_order_90d"]
    ui.kpi_card(c1, "2nd order within 90 days · main KPI", f"{so:.0%}",
                f"Target {so_t:.0%} · recent cohort {k['second_order_90d_recent']:.0%} "
                f"(n={k['second_order_90d_recent_n']})",
                status="good" if so >= so_t else "warn", progress=so / 0.5, target_mark=so_t / 0.5, primary=True)
    cr, cr_t = k["cancel_return_rate_last90"], t["cancel_return_rate"]
    ui.kpi_card(c2, "Cancelled + returned · last 90 days", f"{cr:.1%}",
                f"Target ≤ {cr_t:.1%} · all-time {k['cancel_return_rate']:.1%}",
                status="good" if cr <= cr_t else "warn", progress=cr / 0.15, target_mark=cr_t / 0.15)
    ui.kpi_card(c3, "Regulars drifting / lapsed", f"{k['drifting']} / {k['lapsed']}",
                f"{kes(k['winback_pool_revenue'])} of past revenue to win back", status="info")
    gm, gm_t = k["gross_margin"], t["gross_margin"]
    ui.kpi_card(c4, "Gross margin (estimate)", f"{gm:.0%}", f"Hold at ≥ {gm_t:.0%}",
                status="good" if gm >= gm_t - 0.005 else "warn", progress=gm, target_mark=gm_t)
    st.write("")
    c5, c6, c7, c8 = st.columns(4)
    ui.kpi_card(c5, "Customers who ordered again", f"{k['repeat_rate']:.0%}", "All customers, all time")
    ui.kpi_card(c6, "New customers per month", f"{k['new_customers_per_month_last3']:.0f}",
                "Average of the last 3 full months")
    ui.kpi_card(c7, "First-timers in the nudge window", f"{k['new_in_nudge_window']}", "0–90 days since first order")
    ui.kpi_card(c8, "Delivered revenue", kes(k["delivered_revenue"]), "All time, excl. delivery fees")
    st.caption(f"Drifting = silent longer than {res.drift_days:.0f} days (75% of repeat gaps are shorter); "
               f"Lapsed = longer than {res.lapse_days:.0f} days (90%). Bars show progress; the dark tick is the "
               "target. Small recent cohorts move a lot week to week — judge the trend over a month.")

    st.markdown("#### Orders per month: new vs returning customers")
    o = orders.sort_values("order_datetime").copy()
    o["Customer"] = (o.groupby("customer_id").cumcount() == 0).map({True: "First order", False: "Repeat order"})
    o["Month"] = o["order_datetime"].dt.to_period("M").dt.to_timestamp()
    monthly = o.groupby(["Month", "Customer"]).size().reset_index(name="Orders")
    pick = alt.selection_point(fields=["Customer"], bind="legend")
    brush = alt.selection_interval(encodings=["x"])
    base = alt.Chart(monthly).encode(
        x=alt.X("yearmonth(Month):T", title=None),
        color=alt.Color("Customer:N", scale=alt.Scale(domain=["First order", "Repeat order"],
                                                      range=[BRAND, "#C9B8D9"]),
                        legend=alt.Legend(orient="top", title="Click to highlight")))
    bars = base.mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
        y=alt.Y("sum(Orders):Q", title="Orders"),
        opacity=alt.condition(pick, alt.value(1), alt.value(0.25)),
        tooltip=[alt.Tooltip("yearmonth(Month):T", title="Month"), "Customer:N", "Orders:Q"],
    ).add_params(pick, brush).properties(height=280)
    st.altair_chart(bars, width="stretch")
    st.caption("Hover a bar for the numbers; click the legend to highlight one group; drag across months to select a period.")

# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
with tab_act:
    a = res.actions
    to_msg = a[a["group"] == "Message"]
    st.subheader(f"{len(to_msg)} customers to contact this week")
    nud = to_msg["action"].str.startswith("Reorder").sum()
    ui.chips([("Reorder nudges", str(int(nud))), ("Win-backs", str(int(len(to_msg) - nud))),
              ("Hold-out (don't message)", str(int(len(a) - len(to_msg)))),
              ("Consent unknown", str(int(to_msg["whatsapp_consent"].astype(str).str.startswith(("Unknown", "Not")).sum())))],
             warn={3})
    ui.howto("<b>How to use:</b> work top to bottom (most valuable first). Message on WhatsApp <b>only if consent "
             "is 'Yes'</b> — if it says <i>Unknown</i>, ask first or skip. <b>Don't message hold-out rows</b>: they "
             "prove whether the messages work.")
    st.write("")
    f1, f2, f3 = st.columns([2, 1.2, 1])
    kinds = f1.multiselect("Show", sorted(a["action"].unique()), default=sorted(a["action"].unique()))
    find = f2.text_input("Find a customer code", placeholder="e.g. C03897")
    show_hold = f3.toggle("Show hold-out rows", value=False)

    # Progress is kept for this session, per scoring date, so staff can tick customers off as they go
    key = f"progress_{k['snapshot_date']}"
    if key not in st.session_state:
        st.session_state[key] = pd.DataFrame({"customer_id": a["customer_id"], "done": False, "reply": "—"})
    prog = st.session_state[key]
    work = a.merge(prog, on="customer_id", how="left")
    view = work[work["action"].isin(kinds)]
    if not show_hold:
        view = view[view["group"] == "Message"]
    if find:
        view = view[view["customer_id"].str.contains(find.strip(), case=False)]

    edited = st.data_editor(
        view[["done", "customer_id", "action", "whatsapp_consent", "suggested_message", "reply", "days_silent",
              "orders", "lifetime_revenue", "status", "group", "first_channel", "county"]],
        width="stretch", hide_index=True, height=460, key=f"editor_{key}",
        disabled=["customer_id", "action", "whatsapp_consent", "suggested_message", "days_silent", "orders",
                  "lifetime_revenue", "status", "group", "first_channel", "county"],
        column_config={
            "done": st.column_config.CheckboxColumn("Sent", help="Tick when the message has been sent"),
            "customer_id": st.column_config.TextColumn("Customer", pinned=True),
            "action": "Action", "whatsapp_consent": "WhatsApp consent",
            "suggested_message": st.column_config.TextColumn("Suggested message", width="large"),
            "reply": st.column_config.SelectboxColumn("Reply", options=["—", "Ordered", "Interested", "No reply",
                                                                         "STOP (opt out)"]),
            "days_silent": "Days quiet", "orders": "Orders",
            "lifetime_revenue": st.column_config.NumberColumn("Lifetime spend (KES)", format="localized"),
            "status": "Status", "group": "Group", "first_channel": "First channel", "county": "County"})
    upd = prog.set_index("customer_id")
    upd.loc[edited["customer_id"], ["done", "reply"]] = edited[["done", "reply"]].values
    st.session_state[key] = upd.reset_index()
    prog = st.session_state[key]
    msg_ids = set(to_msg["customer_id"])
    sent = int(prog[prog["customer_id"].isin(msg_ids)]["done"].sum())
    stops = int((prog["reply"] == "STOP (opt out)").sum())
    st.progress(sent / max(len(msg_ids), 1), text=f"{sent} of {len(msg_ids)} messages sent this week"
                + (f" · {stops} opted out: record them in the consent register today" if stops else ""))
    out = a.merge(prog, on="customer_id", how="left")
    st.download_button("⬇️ Download list with progress (incl. hold-out)", out.to_csv(index=False).encode(),
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
    st.caption("Click a segment bar to list its customers below; click empty space to clear.")
    seg_long = seg.melt(id_vars="Segment", value_vars=["customer_share", "revenue_share"],
                        var_name="Measure", value_name="Share")
    seg_long["Measure"] = seg_long["Measure"].map({"customer_share": "Share of customers",
                                                   "revenue_share": "Share of revenue"})
    chosen = alt.selection_point(fields=["Segment"], name="seg_pick")
    ch = (alt.Chart(seg_long).mark_bar(cornerRadiusEnd=4)
          .encode(y=alt.Y("Segment:N", sort=p.SEG_ORDER, title=None),
                  yOffset="Measure:N",
                  x=alt.X("Share:Q", title=None, axis=alt.Axis(format=".0%")),
                  color=alt.Color("Measure:N", scale=alt.Scale(range=["#C9B8D9", BRAND]),
                                  legend=alt.Legend(orient="top", title=None)),
                  opacity=alt.condition(chosen, alt.value(1), alt.value(0.3)),
                  tooltip=["Segment", "Measure", alt.Tooltip("Share:Q", format=".0%")])
          .add_params(chosen).properties(height=320))
    ev = st.altair_chart(ch, width="stretch", on_select="rerun", key="seg_chart")
    picked = []
    try:
        picked = [d.get("Segment") for d in ev.selection.get("seg_pick", []) if d.get("Segment")]
    except Exception:
        picked = []
    life_view = res.lifecycle.reset_index().rename(columns={"index": "customer_id"})
    if picked:
        sub = life_view[life_view["Segment"].isin(picked)]
        st.markdown(f"**{', '.join(picked)}: {len(sub)} customers**")
        st.dataframe(sub[["customer_id", "status", "delivered_orders", "total_revenue", "days_since_last_order",
                          "first_channel", "county"]].sort_values("total_revenue", ascending=False),
                     width="stretch", hide_index=True, height=260,
                     column_config={"customer_id": "Customer", "status": "Status", "delivered_orders": "Orders",
                                    "total_revenue": st.column_config.NumberColumn("Spend (KES)", format="localized"),
                                    "days_since_last_order": "Days quiet", "first_channel": "First channel",
                                    "county": "County"})
    nice = seg.rename(columns={"customers": "Customers", "avg_recency_days": "Avg days since last order",
                               "avg_orders": "Avg orders", "avg_revenue": "Avg spend (KES)",
                               "total_revenue": "Total spend (KES)", "customer_share": "Share of customers",
                               "revenue_share": "Share of revenue"})
    st.dataframe(nice.style.format({"Customers": "{:,.0f}", "Avg days since last order": "{:,.0f}",
                                    "Avg orders": "{:.1f}", "Avg spend (KES)": "{:,.0f}",
                                    "Total spend (KES)": "{:,.0f}", "Share of customers": "{:.0%}",
                                    "Share of revenue": "{:.0%}"}),
                 width="stretch", hide_index=True)
    cs = res.lifecycle[["status", "Segment", "delivered_orders", "total_revenue", "days_since_last_order",
                        "first_channel", "county", "age_group"]]
    st.download_button("⬇️ Download every customer's status", cs.to_csv().encode(),
                       f"customer_status_{k['snapshot_date']}.csv", "text/csv")

# ---------------------------------------------------------------------------
# Customer lookup
# ---------------------------------------------------------------------------
with tab_cust:
    st.subheader("Look up a customer")
    ids = sorted(res.lifecycle.index)
    default_id = res.actions["customer_id"].iloc[0] if len(res.actions) else ids[0]
    cid = st.selectbox("Customer code", ids, index=ids.index(default_id) if default_id in ids else 0)
    row = res.lifecycle.loc[cid]
    h = orders[orders["customer_id"] == cid].sort_values("order_datetime")
    c1, c2, c3, c4 = st.columns(4)
    ui.kpi_card(c1, "Status", str(row["status"]), f"Segment: {row.get('Segment', '—')}")
    ui.kpi_card(c2, "Delivered orders", f"{int(row['delivered_orders'])}", f"{len(h)} placed in total")
    ui.kpi_card(c3, "Lifetime spend", kes(row["total_revenue"]), "Delivered orders only")
    dsl = row["days_since_last_order"]
    ui.kpi_card(c4, "Days since last order", "—" if pd.isna(dsl) else f"{int(dsl)}",
                f"Drifting after {res.drift_days:.0f}")
    on_list = res.actions[res.actions["customer_id"] == cid]
    if len(on_list):
        r0 = on_list.iloc[0]
        st.info(f"On this week's list: **{r0['action']}** · {r0['group']} · consent: {r0['whatsapp_consent']}  \n"
                f"Suggested: {r0['suggested_message']}")
    tl = alt.Chart(h).mark_circle(size=220, opacity=0.9).encode(
        x=alt.X("order_datetime:T", title=None),
        y=alt.Y("product_type:N", title=None),
        color=alt.Color("order_status:N", scale=alt.Scale(domain=["Delivered", "Cancelled", "Returned"],
                                                          range=[BRAND, "#C9831A", "#9AA0A6"]),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("order_datetime:T", title="Date", format="%d %b %Y"), "product_type:N",
                 "sales_channel:N", "order_status:N", alt.Tooltip("net_revenue:Q", format=",.0f", title="KES")])
    st.altair_chart(tl.properties(height=90 + 55 * max(h["product_type"].nunique(), 1),
                                  title="Order history (hover a dot)"), width="stretch")

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
    cpc = a3.number_input("Cost per message incl. perk (KES)", 0, 2000, 50, 10)
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
    curve = pd.DataFrame({"Uplift": [u / 100 for u in range(0, 41)]})
    curve["Net benefit (KES)"] = [p.roi_scenario(contacted, u, opw, aov, margin, cpc, fixed)["net_benefit"]
                                  for u in curve["Uplift"]]
    now = pd.DataFrame({"Uplift": [rr], "Net benefit (KES)": [r_["net_benefit"]]})
    be = r_["break_even_response_rate"]
    line = alt.Chart(curve).mark_line(color=BRAND, strokeWidth=3).encode(
        x=alt.X("Uplift:Q", axis=alt.Axis(format=".0%"), title="Uplift (extra share who return because of the message)"),
        y=alt.Y("Net benefit (KES):Q", axis=alt.Axis(format=",.0f")),
        tooltip=[alt.Tooltip("Uplift:Q", format=".0%"), alt.Tooltip("Net benefit (KES):Q", format=",.0f")])
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color="#999").encode(y="y:Q")
    dot = alt.Chart(now).mark_point(size=160, filled=True, color="#C9831A").encode(
        x="Uplift:Q", y="Net benefit (KES):Q",
        tooltip=[alt.Tooltip("Uplift:Q", format=".0%", title="Your uplift"),
                 alt.Tooltip("Net benefit (KES):Q", format=",.0f")])
    layers = [zero, line, dot]
    if be == be and 0 <= be <= 0.4:
        layers.append(alt.Chart(pd.DataFrame({"x": [be]})).mark_rule(strokeDash=[4, 4], color=BRAND).encode(x="x:Q"))
    st.altair_chart(alt.layer(*layers).properties(height=280,
                    title="Net benefit as uplift changes (orange dot = your setting, dashed line = break-even)"),
                    width="stretch")
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
