"""
Immaculate Vibes – Retention Action Engine (scoring pipeline)
=============================================================

This module is the *deployment* version of the analysis in
`Customer_Retention_Analysis.ipynb`. The notebook explored the data once;
this module turns the same business rules into functions that can be run
every week on a fresh export of the sales sheet, without anyone opening
the notebook.

What it produces on every run
-----------------------------
1. A data-quality report (is the new export safe to use?)
2. RFM segments for every customer with a delivered order
3. A lifecycle status for every customer (New / Active / Drifting / Lapsed ...)
4. This week's ACTION LIST:
     * reorder nudges for first-time buyers at day 30, 60 and 90
     * a win-back list of drifting / lapsed regulars, most valuable first
   with a fixed random hold-out (control) group so the effect can be measured.
5. KPI snapshot (primary KPI: second order within 90 days)

All thresholds live in `DEFAULT_CONFIG` so the business can change them
without touching the logic. Every rule here is the same rule used in the
notebook (see the comments that point back to notebook sections).
"""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_CONFIG: dict = {
    # Snapshot ("as of") date. None = the day after the last order in the file,
    # which is what the notebook used. In live use you can pass today's date.
    "snapshot_date": None,
    # Drift / lapse thresholds in days of silence. "auto" = 75th / 90th
    # percentile of observed repurchase gaps (notebook 8.4 -> 209 / 351 days).
    "drift_days": "auto",
    "lapse_days": "auto",
    "drift_quantile": 0.75,
    "lapse_quantile": 0.90,
    # Reorder-nudge steps for first-time customers (report: days 30/60/90).
    "nudge_days": [30, 60, 90],
    # A nudge is "due" if the customer is between step and step+window days.
    # 7 = the job runs weekly, so nobody is skipped or messaged twice.
    "nudge_window_days": 7,
    # Share of each list held back as a control group (no message) so the
    # uplift can be measured honestly (report Section 4.3, "control groups").
    "holdout_share": 0.20,
    "holdout_salt": "iv-2027",
    # Targets from the strategy report (Table 4) for the KPI panel.
    "targets": {
        "second_order_90d": 0.35,
        "cancel_return_rate": 0.065,
        "winback_rate": 0.15,
        "gross_margin": 0.49,
    },
}

REQUIRED_COLUMNS = [
    "order_id", "order_datetime", "customer_id", "customer_gender",
    "customer_age_group", "customer_county", "sales_channel", "product_type",
    "order_total", "order_status", "net_revenue", "order_number_for_customer",
]
OPTIONAL_COLUMNS = ["gross_profit_estimate", "cost_estimate", "customer_type"]
VALID_STATUSES = {"Delivered", "Cancelled", "Returned"}

SEG_ORDER = ["Champions", "Loyal", "New/Promising", "Need Attention", "At Risk", "Lost"]


def make_config(**overrides) -> dict:
    cfg = {**DEFAULT_CONFIG, "targets": dict(DEFAULT_CONFIG["targets"])}
    for k, v in overrides.items():
        if v is not None:
            cfg[k] = v
    return cfg


# ---------------------------------------------------------------------------
# 1. Load and validate  (notebook section 2.1 – 2.4)
# ---------------------------------------------------------------------------
class DataValidationError(ValueError):
    """Raised when the export cannot be used at all."""


def load_orders(source) -> pd.DataFrame:
    """Read the sales export (path, file-like object or bytes).

    Only truly empty cells are treated as missing (as in the notebook), so a
    genuine text value is never silently turned into a blank.
    """
    if isinstance(source, (bytes, bytearray)):
        source = io.BytesIO(source)
    df = pd.read_csv(source, keep_default_na=False, na_values=[""])
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise DataValidationError(f"The file is missing required columns: {missing}")
    df["order_datetime"] = pd.to_datetime(df["order_datetime"], errors="coerce")
    for c in ["order_total", "net_revenue", "gross_profit_estimate", "cost_estimate"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["order_number_for_customer"] = pd.to_numeric(df["order_number_for_customer"], errors="coerce")
    return df


def quality_report(df: pd.DataFrame) -> dict:
    """Health checks run on every export. 'blocking' issues stop the run;
    'warnings' are shown to the user but scoring continues."""
    blocking, warnings = [], []

    bad_dates = int(df["order_datetime"].isna().sum())
    if bad_dates:
        blocking.append(f"{bad_dates} rows have an unreadable order_datetime")
    dup = int(df["order_id"].duplicated().sum())
    if dup:
        blocking.append(f"{dup} duplicate order_id values")
    bad_status = sorted(set(df["order_status"].dropna()) - VALID_STATUSES)
    if bad_status:
        blocking.append(f"Unknown order_status values: {bad_status}")
    if df["customer_id"].isna().any():
        blocking.append(f"{int(df['customer_id'].isna().sum())} rows have no customer_id")

    # Key revenue assumption from notebook 2.4
    leak = df.loc[df["order_status"] != "Delivered", "net_revenue"].fillna(0).ne(0).sum()
    if leak:
        warnings.append(f"{int(leak)} cancelled/returned orders carry non-zero net_revenue "
                        "(they are excluded from revenue anyway)")
    neg = int((df["net_revenue"] < 0).sum())
    if neg:
        warnings.append(f"{neg} rows have negative net_revenue")

    # Sequence check (notebook 2.2)
    seq = df.sort_values(["customer_id", "order_datetime"]).groupby("customer_id").cumcount() + 1
    seq_match = float((seq.reindex(df.index) == df["order_number_for_customer"]).mean()) if len(df) else 1.0
    if seq_match < 0.99:
        warnings.append(f"order_number_for_customer matches the real date order on only {seq_match:.1%} of rows; "
                        "the pipeline recomputes order sequence from dates")

    for col in ["customer_county", "customer_age_group"]:
        n = int((df.groupby("customer_id")[col].nunique() > 1).sum())
        if n:
            warnings.append(f"{n} customers have more than one {col}; the first value is used")

    # Missing months (gaps in the record)
    if df["order_datetime"].notna().any():
        months = df["order_datetime"].dt.to_period("M")
        full = pd.period_range(months.min(), months.max(), freq="M")
        gap_months = [str(m) for m in full if m not in set(months)]
        if gap_months:
            warnings.append(f"No orders at all in: {', '.join(gap_months)} (missing data?)")

    # Outliers on order value (IQR rule) – reported, not removed
    q1, q3 = df["order_total"].quantile([0.25, 0.75])
    hi = q3 + 3 * (q3 - q1)
    n_out = int((df["order_total"] > hi).sum())
    if n_out:
        warnings.append(f"{n_out} orders above KES {hi:,.0f} (extreme value check) – kept, please confirm")

    first, last = df["order_datetime"].min(), df["order_datetime"].max()
    return {
        "rows": int(len(df)),
        "customers": int(df["customer_id"].nunique()),
        "first_order": None if pd.isna(first) else first.strftime("%Y-%m-%d"),
        "last_order": None if pd.isna(last) else last.strftime("%Y-%m-%d"),
        "sequence_match_rate": round(seq_match, 4),
        "status_counts": df["order_status"].value_counts().to_dict(),
        "blocking": blocking,
        "warnings": warnings,
        "ok": not blocking,
    }


def resolve_snapshot(df: pd.DataFrame, cfg: dict) -> pd.Timestamp:
    if cfg.get("snapshot_date"):
        return pd.Timestamp(cfg["snapshot_date"]).normalize()
    return df["order_datetime"].max().normalize() + pd.Timedelta(days=1)


def restrict_to_snapshot(df: pd.DataFrame, snapshot: pd.Timestamp) -> pd.DataFrame:
    """Keep only orders placed before the snapshot (used for back-testing)."""
    return df[df["order_datetime"] < snapshot].copy()


# ---------------------------------------------------------------------------
# 2. Customer table  (notebook 2.7)
# ---------------------------------------------------------------------------
def build_customers(df: pd.DataFrame) -> pd.DataFrame:
    s = df.sort_values(["customer_id", "order_datetime"])
    customers = s.groupby("customer_id").agg(
        orders_placed=("order_id", "nunique"),
        first_order=("order_datetime", "min"),
        last_order=("order_datetime", "max"),
        first_channel=("sales_channel", "first"),
        gender=("customer_gender", "first"),
        age_group=("customer_age_group", "first"),
        county=("customer_county", "first"),
    )
    second = s.groupby("customer_id")["order_datetime"].nth(1)
    second.index = s.loc[second.index, "customer_id"].values
    customers["second_order"] = second.reindex(customers.index)
    customers["days_to_second_order"] = (customers["second_order"] - customers["first_order"]).dt.days

    delivered = df[df["order_status"] == "Delivered"]
    agg = {"delivered_orders": ("order_id", "nunique"), "total_revenue": ("net_revenue", "sum"),
           "last_delivered": ("order_datetime", "max"), "first_delivered": ("order_datetime", "min")}
    if "gross_profit_estimate" in df.columns:
        agg["total_gross_profit"] = ("gross_profit_estimate", "sum")
    rev = delivered.groupby("customer_id").agg(**agg)
    customers = customers.join(rev)
    customers[["delivered_orders", "total_revenue"]] = customers[["delivered_orders", "total_revenue"]].fillna(0)
    customers["is_repeat_customer"] = customers["orders_placed"] > 1
    return customers


# ---------------------------------------------------------------------------
# 3. RFM segmentation  (notebook 4.1 – 4.3)
# ---------------------------------------------------------------------------
def assign_segment(r: int, f: int, m: int) -> str:
    # Order matters: At Risk is checked BEFORE Loyal (see notebook 4.3)
    if r >= 4 and f >= 4:
        return "Champions"
    if r <= 2 and f >= 3:
        return "At Risk"
    if f >= 3 and m >= 3:
        return "Loyal"
    if r >= 4 and f <= 2:
        return "New/Promising"
    if r <= 2 and f <= 2:
        return "Lost"
    return "Need Attention"


def _quintile(rank: pd.Series, labels) -> pd.Series:
    n = len(rank)
    if n >= 5:
        return pd.qcut(rank, 5, labels=labels).astype(int)
    # Very small files: fall back to a proportional score so the app never crashes
    pct = (rank - 0.5) / max(n, 1)
    idx = np.minimum((pct * 5).astype(int), 4)
    return pd.Series(np.array(labels)[idx], index=rank.index).astype(int)


def score_rfm(df: pd.DataFrame, snapshot: pd.Timestamp) -> pd.DataFrame:
    delivered = df[df["order_status"] == "Delivered"]
    rfm = delivered.groupby("customer_id").agg(
        last=("order_datetime", "max"),
        Frequency=("order_id", "nunique"),
        Monetary=("net_revenue", "sum"),
    ).reset_index()
    if rfm.empty:
        return rfm.assign(Recency=[], R_score=[], F_score=[], M_score=[], RFM_score=[], Segment=[])
    rfm["Recency"] = (snapshot - rfm["last"]).dt.days
    rfm = rfm.drop(columns="last")[["customer_id", "Recency", "Frequency", "Monetary"]]

    rfm["R_score"] = _quintile(rfm["Recency"].rank(method="first"), [5, 4, 3, 2, 1])
    freq_rank = (rfm["Frequency"] + rfm["Monetary"] / (rfm["Monetary"].max() * 10)).rank(method="first")
    rfm["F_score"] = _quintile(freq_rank, [1, 2, 3, 4, 5])
    rfm["M_score"] = _quintile(rfm["Monetary"].rank(method="first"), [1, 2, 3, 4, 5])
    rfm["RFM_score"] = rfm["R_score"].astype(str) + rfm["F_score"].astype(str) + rfm["M_score"].astype(str)
    rfm["Segment"] = [assign_segment(r, f, m) for r, f, m in zip(rfm.R_score, rfm.F_score, rfm.M_score)]
    return rfm


def segment_summary(rfm: pd.DataFrame) -> pd.DataFrame:
    seg = (rfm.groupby("Segment").agg(customers=("customer_id", "count"),
                                      avg_recency_days=("Recency", "mean"),
                                      avg_orders=("Frequency", "mean"),
                                      avg_revenue=("Monetary", "mean"),
                                      total_revenue=("Monetary", "sum"))
           .reindex(SEG_ORDER).fillna(0))
    seg["customer_share"] = seg["customers"] / max(seg["customers"].sum(), 1)
    seg["revenue_share"] = seg["total_revenue"] / max(seg["total_revenue"].sum(), 1)
    return seg


# ---------------------------------------------------------------------------
# 4. Repurchase rhythm and lifecycle status  (notebook 8.3 – 8.4)
# ---------------------------------------------------------------------------
def repurchase_gaps(df: pd.DataFrame) -> pd.Series:
    d = df[df["order_status"] == "Delivered"].sort_values(["customer_id", "order_datetime"])
    return d.groupby("customer_id")["order_datetime"].diff().dt.days.dropna()


def thresholds(df: pd.DataFrame, cfg: dict) -> tuple[float, float]:
    gaps = repurchase_gaps(df)
    drift = cfg["drift_days"]
    lapse = cfg["lapse_days"]
    if drift == "auto":
        drift = float(gaps.quantile(cfg["drift_quantile"])) if len(gaps) else 209.0
    if lapse == "auto":
        lapse = float(gaps.quantile(cfg["lapse_quantile"])) if len(gaps) else 351.0
    return float(drift), float(lapse)


def lifecycle(customers: pd.DataFrame, snapshot: pd.Timestamp, drift: float, lapse: float,
              cfg: dict) -> pd.DataFrame:
    """One lifecycle status per customer.

    Repeat customers (2+ delivered orders): Active / Drifting / Lapsed, using
    days since last delivered order vs the drift and lapse thresholds.
    One-time customers (exactly 1 delivered order): 'New (in nudge window)'
    for the first 90 days, then 'One-time (dormant)'.
    Customers with no delivered order: 'No delivered order'.
    """
    c = customers.copy()
    c["days_since_last_order"] = (snapshot - c["last_delivered"]).dt.days
    c["days_since_first_order"] = (snapshot - c["first_delivered"]).dt.days
    last_step = max(cfg["nudge_days"])

    status = np.select(
        [
            c["delivered_orders"] == 0,
            (c["delivered_orders"] == 1) & (c["days_since_first_order"] <= last_step + cfg["nudge_window_days"]),
            c["delivered_orders"] == 1,
            c["days_since_last_order"] <= drift,
            c["days_since_last_order"] <= lapse,
        ],
        ["No delivered order", "New (in nudge window)", "One-time (dormant)", "Active", "Drifting"],
        default="Lapsed",
    )
    c["status"] = status
    return c


def in_holdout(customer_id: str, share: float, salt: str) -> bool:
    """Deterministic random hold-out: the same customer is always in the same
    group for a given campaign salt, so results can be compared fairly."""
    h = hashlib.sha256(f"{salt}:{customer_id}".encode()).hexdigest()
    return (int(h[:8], 16) % 10_000) / 10_000 < share


# ---------------------------------------------------------------------------
# 5. Action lists
# ---------------------------------------------------------------------------
NUDGE_MESSAGES = {
    30: "Day 30 – Thank-you + styling tip for their {product}; ask how the fit was.",
    60: "Day 60 – Preview of the new collection / restock that matches their {product}.",
    90: "Day 90 – Small second-order perk (e.g. free delivery) valid for 14 days.",
}
WINBACK_MESSAGES = {
    "Drifting": "We miss you – early access to the new drop + free delivery on your next order.",
    "Lapsed": "Personal note from the owner + 'what can we do better?' + one-time welcome-back perk.",
}


def _attach_consent(df: pd.DataFrame, consent: pd.DataFrame | None) -> pd.DataFrame:
    if consent is None or consent.empty:
        df["whatsapp_consent"] = "Unknown – confirm before messaging"
        return df
    con = consent.copy()
    con.columns = [c.strip().lower() for c in con.columns]
    col = next((c for c in con.columns if "consent" in c or "opt" in c), None)
    if col is None or "customer_id" not in con.columns:
        df["whatsapp_consent"] = "Unknown – consent file not understood"
        return df
    yes = con[col].astype(str).str.strip().str.lower().isin({"yes", "y", "true", "1", "opt-in", "opted in"})
    m = dict(zip(con["customer_id"], np.where(yes, "Yes", "No")))
    df["whatsapp_consent"] = df["customer_id"].map(m).fillna("Not in register")
    return df


def build_action_list(df: pd.DataFrame, life: pd.DataFrame, cfg: dict,
                      consent: pd.DataFrame | None = None) -> pd.DataFrame:
    """Everyone who should hear from Immaculate Vibes this week, in priority order."""
    last_product = (df[df["order_status"] == "Delivered"].sort_values("order_datetime")
                    .groupby("customer_id")["product_type"].last())
    window = cfg["nudge_window_days"]
    rows = []

    # (a) Reorder nudges for first-time buyers
    new = life[life["delivered_orders"] == 1]
    for cid, r in new.iterrows():
        d = r["days_since_first_order"]
        for step in cfg["nudge_days"]:
            if step <= d < step + window:
                prod = last_product.get(cid, "outfit")
                rows.append({"customer_id": cid, "action": f"Reorder nudge – day {step}",
                             "priority": 1 if step == 90 else 2,
                             "status": r["status"], "days_silent": int(d),
                             "orders": int(r["delivered_orders"]), "lifetime_revenue": r["total_revenue"],
                             "suggested_message": NUDGE_MESSAGES[step].format(product=prod),
                             "first_channel": r["first_channel"], "county": r["county"]})

    # (b) Win-back for drifting / lapsed regulars, worth-first
    wb = life[life["status"].isin(["Drifting", "Lapsed"])]
    for cid, r in wb.iterrows():
        rows.append({"customer_id": cid, "action": f"Win-back – {r['status'].lower()}",
                     "priority": 1 if r["status"] == "Drifting" else 3,
                     "status": r["status"], "days_silent": int(r["days_since_last_order"]),
                     "orders": int(r["delivered_orders"]), "lifetime_revenue": r["total_revenue"],
                     "suggested_message": WINBACK_MESSAGES[r["status"]],
                     "first_channel": r["first_channel"], "county": r["county"]})

    cols = ["customer_id", "action", "priority", "status", "days_silent", "orders", "lifetime_revenue",
            "suggested_message", "first_channel", "county"]
    out = pd.DataFrame(rows, columns=cols)
    if out.empty:
        out["group"] = []
        out["whatsapp_consent"] = []
        return out
    out["group"] = np.where(
        [in_holdout(c, cfg["holdout_share"], cfg["holdout_salt"]) for c in out["customer_id"]],
        "Hold-out (do NOT message)", "Message")
    out = _attach_consent(out, consent)
    out = out.sort_values(["priority", "lifetime_revenue"], ascending=[True, False]).reset_index(drop=True)
    return out


# ---------------------------------------------------------------------------
# 6. KPIs  (report Table 4)
# ---------------------------------------------------------------------------
def second_order_rate(customers: pd.DataFrame, snapshot: pd.Timestamp, within: int = 90,
                      cohort_start: pd.Timestamp | None = None) -> tuple[float, int]:
    """Share of customers who placed a 2nd order within `within` days of their first.
    Only customers whose first order is at least `within` days before the
    snapshot are counted (otherwise they haven't had the chance yet)."""
    elig = customers[customers["first_order"] <= snapshot - pd.Timedelta(days=within)]
    if cohort_start is not None:
        elig = elig[elig["first_order"] >= cohort_start]
    if elig.empty:
        return float("nan"), 0
    second_in_time = (elig["days_to_second_order"] <= within) & (elig["second_order"] < snapshot)
    return float(second_in_time.mean()), int(len(elig))


def compute_kpis(df: pd.DataFrame, customers: pd.DataFrame, life: pd.DataFrame,
                 snapshot: pd.Timestamp, drift: float, lapse: float) -> dict:
    delivered = df[df["order_status"] == "Delivered"]
    rate_all, n_all = second_order_rate(customers, snapshot, 90)
    # Rolling: customers whose first order was 90–180 days ago (shows recent change)
    rate_recent, n_recent = second_order_rate(customers, snapshot, 90,
                                              cohort_start=snapshot - pd.Timedelta(days=180))
    last90 = df[df["order_datetime"] >= snapshot - pd.Timedelta(days=90)]
    cr_all = float((df["order_status"] != "Delivered").mean())
    cr_90 = float((last90["order_status"] != "Delivered").mean()) if len(last90) else float("nan")

    first_orders = customers["first_order"]
    month_start = snapshot.to_period("M").to_timestamp()
    last3 = first_orders[(first_orders >= month_start - pd.DateOffset(months=3)) & (first_orders < month_start)]

    gm = float("nan")
    if "gross_profit_estimate" in delivered.columns and delivered["net_revenue"].sum() > 0:
        gm = float(delivered["gross_profit_estimate"].sum() / delivered["net_revenue"].sum())

    st = life["status"].value_counts()
    wb = life[life["status"].isin(["Drifting", "Lapsed"])]
    return {
        "snapshot_date": snapshot.strftime("%Y-%m-%d"),
        "customers": int(len(customers)),
        "orders": int(df["order_id"].nunique()),
        "delivered_revenue": float(delivered["net_revenue"].sum()),
        "repeat_rate": float(customers["is_repeat_customer"].mean()),
        "second_order_90d": rate_all, "second_order_90d_n": n_all,
        "second_order_90d_recent": rate_recent, "second_order_90d_recent_n": n_recent,
        "cancel_return_rate": cr_all, "cancel_return_rate_last90": cr_90,
        "new_customers_per_month_last3": float(len(last3) / 3),
        "gross_margin": gm,
        "drift_days": drift, "lapse_days": lapse,
        "active": int(st.get("Active", 0)), "drifting": int(st.get("Drifting", 0)),
        "lapsed": int(st.get("Lapsed", 0)),
        "new_in_nudge_window": int(st.get("New (in nudge window)", 0)),
        "winback_pool_revenue": float(wb["total_revenue"].sum()),
    }


def winback_conversion(df: pd.DataFrame, previous_list: pd.DataFrame, since: str | pd.Timestamp) -> pd.DataFrame:
    """Monitoring: of the customers on an earlier action list, how many have
    ordered since? Split by Message vs Hold-out so the uplift is visible."""
    since = pd.Timestamp(since)
    recent = df[(df["order_datetime"] >= since) & (df["order_status"] != "Cancelled")]
    came_back = set(recent["customer_id"])
    p = previous_list.copy()
    p["ordered_since"] = p["customer_id"].isin(came_back)
    grp_col = "group" if "group" in p.columns else None
    p["action_type"] = p["action"].str.split(" – ").str[0]
    by = ["action_type"] + ([grp_col] if grp_col else [])
    return p.groupby(by).agg(customers=("customer_id", "count"),
                             came_back=("ordered_since", "sum"),
                             rate=("ordered_since", "mean")).reset_index()


# ---------------------------------------------------------------------------
# 7. Scenario / ROI calculator  (report Section 4.3)
# ---------------------------------------------------------------------------
def roi_scenario(contacted: int, response_rate: float, orders_per_win: float, aov: float,
                 margin: float, cost_per_contact: float, fixed_cost: float = 0.0) -> dict:
    wins = contacted * response_rate
    revenue = wins * orders_per_win * aov
    gross_profit = revenue * margin
    cost = contacted * cost_per_contact + fixed_cost
    return {"customers_won": wins, "extra_revenue": revenue, "extra_gross_profit": gross_profit,
            "cost": cost, "net_benefit": gross_profit - cost,
            "break_even_response_rate": (cost / (contacted * orders_per_win * aov * margin))
            if contacted and aov and margin and orders_per_win else float("nan")}


def sensitivity_grid(contacted: int, aov: float, cost_per_contact: float, fixed_cost: float,
                     response_rates: Iterable[float], margins: Iterable[float],
                     orders_per_win: float = 2.0) -> pd.DataFrame:
    rows = []
    for m in margins:
        for rr in response_rates:
            r = roi_scenario(contacted, rr, orders_per_win, aov, m, cost_per_contact, fixed_cost)
            rows.append({"gross_margin": m, "response_rate": rr, "net_benefit": r["net_benefit"]})
    return pd.DataFrame(rows).pivot(index="gross_margin", columns="response_rate", values="net_benefit")


# ---------------------------------------------------------------------------
# 8. Back-test: does the drift/lapse rule pick the right people?
# ---------------------------------------------------------------------------
def backtest_rules(df: pd.DataFrame, as_of: str | pd.Timestamp, horizon_days: int = 180,
                   cfg: dict | None = None) -> pd.DataFrame:
    """Freeze the data at `as_of`, flag repeat customers with each rule, then
    look `horizon_days` ahead to see who actually came back on their own.

    A good win-back rule flags people who would NOT return unaided (so the
    message has something to do) while keeping the list short. Compared with
    the blueprint's naive '14 days silent' rule and with 'message everyone'.
    """
    cfg = cfg or make_config()
    as_of = pd.Timestamp(as_of)
    past = restrict_to_snapshot(df, as_of)
    future = df[(df["order_datetime"] >= as_of) &
                (df["order_datetime"] < as_of + pd.Timedelta(days=horizon_days)) &
                (df["order_status"] != "Cancelled")]
    returned = set(future["customer_id"])
    cust = build_customers(past)
    drift, lapse = thresholds(past, cfg)
    life = lifecycle(cust, as_of, drift, lapse, cfg)
    rep = life[life["delivered_orders"] >= 2].copy()
    rep["returned"] = rep.index.isin(returned)

    rules = {
        "Message every repeat customer": pd.Series(True, index=rep.index),
        "Naive: silent > 14 days (blueprint)": rep["days_since_last_order"] > 14,
        f"Drifting: silent > {drift:.0f} days (P75)": rep["status"] == "Drifting",
        f"Lapsed: silent > {lapse:.0f} days (P90)": rep["status"] == "Lapsed",
        "Drifting + Lapsed (deployed rule)": rep["status"].isin(["Drifting", "Lapsed"]),
        "Active (not flagged)": rep["status"] == "Active",
    }
    out = []
    for name, mask in rules.items():
        g = rep[mask]
        out.append({"rule": name, "customers_flagged": int(len(g)),
                    "share_of_repeat_customers": len(g) / max(len(rep), 1),
                    "returned_unaided_within_horizon": float(g["returned"].mean()) if len(g) else np.nan,
                    "did_not_return": float(1 - g["returned"].mean()) if len(g) else np.nan,
                    "past_revenue_at_stake": float(g["total_revenue"].sum())})
    res = pd.DataFrame(out)
    res.attrs.update({"as_of": as_of.strftime("%Y-%m-%d"), "horizon_days": horizon_days,
                      "drift_days": drift, "lapse_days": lapse, "repeat_customers": int(len(rep))})
    return res


def rolling_backtest(df: pd.DataFrame, as_of_dates: Iterable, horizon_days: int = 180,
                     cfg: dict | None = None) -> pd.DataFrame:
    """Run `backtest_rules` at several past dates and pool the results, so one
    lucky or unlucky month cannot drive the conclusion."""
    frames = []
    for a in as_of_dates:
        bt = backtest_rules(df, a, horizon_days, cfg)
        # Normalise rule names so the same rule lines up across dates
        bt["rule"] = (bt["rule"].str.replace(r"silent > \d+ days \(P75\)", "silent > P75 gap", regex=True)
                                .str.replace(r"silent > \d+ days \(P90\)", "silent > P90 gap", regex=True))
        bt["as_of"] = bt.attrs["as_of"]
        bt["returned_n"] = bt["returned_unaided_within_horizon"] * bt["customers_flagged"]
        frames.append(bt)
    allbt = pd.concat(frames)
    pooled = allbt.groupby("rule", sort=False).agg(flagged=("customers_flagged", "sum"),
                                                   returned=("returned_n", "sum"),
                                                   avg_share_flagged=("share_of_repeat_customers", "mean"))
    pooled["returned_unaided_rate"] = pooled["returned"] / pooled["flagged"]
    # 95% Wilson interval – honest uncertainty for small groups
    n, p, z = pooled["flagged"], pooled["returned_unaided_rate"], 1.96
    centre = (p + z**2 / (2 * n)) / (1 + z**2 / n)
    half = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / (1 + z**2 / n)
    pooled["ci_low"], pooled["ci_high"] = centre - half, centre + half
    return pooled.reset_index(), allbt


# ---------------------------------------------------------------------------
# 9. One call that runs everything
# ---------------------------------------------------------------------------
@dataclass
class RunResult:
    snapshot: pd.Timestamp
    quality: dict
    customers: pd.DataFrame
    rfm: pd.DataFrame
    segments: pd.DataFrame
    lifecycle: pd.DataFrame
    actions: pd.DataFrame
    kpis: dict
    drift_days: float
    lapse_days: float
    config: dict = field(default_factory=dict)


def run(df: pd.DataFrame, cfg: dict | None = None, consent: pd.DataFrame | None = None) -> RunResult:
    cfg = cfg or make_config()
    quality = quality_report(df)
    if not quality["ok"]:
        raise DataValidationError("; ".join(quality["blocking"]))
    snapshot = resolve_snapshot(df, cfg)
    df = restrict_to_snapshot(df, snapshot)
    customers = build_customers(df)
    rfm = score_rfm(df, snapshot)
    drift, lapse = thresholds(df, cfg)
    life = lifecycle(customers, snapshot, drift, lapse, cfg)
    life = life.join(rfm.set_index("customer_id")[["Segment", "RFM_score"]])
    actions = build_action_list(df, life, cfg, consent)
    kpis = compute_kpis(df, customers, life, snapshot, drift, lapse)
    return RunResult(snapshot, quality, customers, rfm, segment_summary(rfm), life, actions, kpis,
                     drift, lapse, cfg)
