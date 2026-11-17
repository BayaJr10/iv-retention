"""Tests: (1) the deployed pipeline reproduces the notebook's numbers on the
real export, (2) rules behave correctly on small hand-made cases,
(3) bad exports are stopped before they reach the action list."""
from pathlib import Path

import pandas as pd
import pytest

from iv_retention import pipeline as p

ROOT = Path(__file__).resolve().parents[1]
REAL = ROOT / "data" / "Immaculate_Vibes_Sales.csv"
SAMPLE = ROOT / "sample_data" / "sample_sales.csv"


# ---------- parity with the notebook (only runs where the real file exists) ----------
@pytest.mark.skipif(not REAL.exists(), reason="real export not present (kept out of git)")
def test_parity_with_notebook():
    r = p.run(p.load_orders(REAL))
    k = r.kpis
    assert k["customers"] == 767 and k["orders"] == 2115
    assert k["delivered_revenue"] == 6_204_650
    assert round(k["repeat_rate"], 3) == 0.619
    assert round(k["second_order_90d"], 2) == 0.24
    assert round(k["cancel_return_rate"], 3) == 0.087
    assert r.drift_days == 209 and round(r.lapse_days) == 351
    assert (k["drifting"], k["lapsed"]) == (101, 40)
    assert k["winback_pool_revenue"] == 1_266_550
    seg = r.segments["customers"]
    assert seg["At Risk"] == 107 and seg["Lost"] == 187 and seg["New/Promising"] == 62
    assert len(r.rfm) == 736


# ---------- rule logic ----------
def test_segment_rules_order():
    assert p.assign_segment(5, 5, 5) == "Champions"
    assert p.assign_segment(1, 4, 5) == "At Risk"      # At Risk wins over Loyal
    assert p.assign_segment(3, 3, 3) == "Loyal"
    assert p.assign_segment(4, 1, 1) == "New/Promising"
    assert p.assign_segment(2, 2, 5) == "Lost"
    assert p.assign_segment(3, 2, 2) == "Need Attention"


def _orders(rows):
    df = pd.DataFrame(rows, columns=["customer_id", "order_datetime", "order_status", "net_revenue"])
    df["order_datetime"] = pd.to_datetime(df["order_datetime"])
    df["order_id"] = [f"O{i}" for i in range(len(df))]
    df["order_total"] = df["net_revenue"].where(df["net_revenue"] > 0, 3000)
    for c, v in {"customer_gender": "Female", "customer_age_group": "25-34", "customer_county": "Nairobi",
                 "sales_channel": "TikTok", "product_type": "Palazzo pants", "gross_profit_estimate": 1500}.items():
        df[c] = v
    df["order_number_for_customer"] = df.sort_values("order_datetime").groupby("customer_id").cumcount() + 1
    return df


def test_lifecycle_and_nudges():
    snap = "2026-06-01"
    df = _orders([
        ("A", "2026-05-01", "Delivered", 3000),             # 31 days ago -> day-30 nudge
        ("B", "2026-03-03", "Delivered", 3000),             # 90 days ago -> day-90 nudge
        ("C", "2025-01-01", "Delivered", 3000), ("C", "2025-02-01", "Delivered", 3000),  # repeat, long silent
        ("D", "2026-05-20", "Delivered", 3000), ("D", "2026-05-25", "Delivered", 3000),  # repeat, active
        ("E", "2026-05-10", "Cancelled", 0),                 # never delivered
    ])
    cfg = p.make_config(snapshot_date=snap, drift_days=100, lapse_days=300, holdout_share=0.0)
    r = p.run(df, cfg)
    st = r.lifecycle["status"]
    assert st["A"] == "New (in nudge window)" and st["C"] == "Lapsed"
    assert st["D"] == "Active" and st["E"] == "No delivered order"
    acts = dict(zip(r.actions["customer_id"], r.actions["action"]))
    assert acts["A"] == "Reorder nudge – day 30"
    assert acts["B"] == "Reorder nudge – day 90"
    assert acts["C"] == "Win-back – lapsed"
    assert "D" not in acts and "E" not in acts


def test_holdout_is_deterministic_and_close_to_share():
    ids = [f"C{i:05d}" for i in range(5000)]
    g1 = [p.in_holdout(i, 0.2, "s") for i in ids]
    g2 = [p.in_holdout(i, 0.2, "s") for i in ids]
    assert g1 == g2
    assert 0.18 < sum(g1) / len(g1) < 0.22


def test_snapshot_excludes_future_orders():
    df = _orders([("A", "2026-01-01", "Delivered", 3000), ("A", "2026-07-01", "Delivered", 3000)])
    r = p.run(df, p.make_config(snapshot_date="2026-03-01"))
    assert r.lifecycle.loc["A", "delivered_orders"] == 1


def test_roi_break_even():
    r = p.roi_scenario(100, 0.10, 2, 3000, 0.5, 10, 0)
    assert r["extra_revenue"] == 60_000 and r["net_benefit"] == 30_000 - 1_000
    assert abs(r["break_even_response_rate"] - 1000 / (100 * 2 * 3000 * 0.5)) < 1e-12


# ---------- bad data is stopped ----------
def test_duplicate_orders_block_the_run():
    df = _orders([("A", "2026-01-01", "Delivered", 3000), ("B", "2026-01-02", "Delivered", 3000)])
    df.loc[1, "order_id"] = df.loc[0, "order_id"]
    with pytest.raises(p.DataValidationError):
        p.run(df)


def test_missing_column_rejected(tmp_path):
    f = tmp_path / "bad.csv"
    pd.DataFrame({"order_id": [1]}).to_csv(f, index=False)
    with pytest.raises(p.DataValidationError):
        p.load_orders(f)


@pytest.mark.skipif(not SAMPLE.exists(), reason="sample data not generated")
def test_sample_data_runs():
    r = p.run(p.load_orders(SAMPLE))
    assert r.kpis["customers"] > 50 and len(r.actions) > 0
