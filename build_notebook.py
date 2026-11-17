"""Builds the deployment-phase version of the analysis notebook from the original."""
import sys
import nbformat as nbf

src, dst = sys.argv[1], sys.argv[2]
nb = nbf.read(src, as_version=4)
for c in nb.cells:
    if c.cell_type == "code":
        c.outputs, c.execution_count = [], None

md = lambda s: nbf.v4.new_markdown_cell(s.strip())
code = lambda s: nbf.v4.new_code_cell(s.strip())

# --- 1. Title table: add Part 10 ------------------------------------------------
c0 = nb.cells[0]
c0.source = c0.source.replace(
    "| 9. Wrap-up | Executive summary, recommendations, limitations |",
    "| 9. Wrap-up | Executive summary, recommendations, limitations |\n"
    "| **10. Deployment** | **Turns the analysis into a weekly Retention Action Engine: parity check, back-test "
    "against a baseline, ROI scenarios, action list, monitoring plan and responsible-use rules** |")
c0.source += ("\n\n> **Deployment version.** Sections 1–9 are the original exploratory analysis, unchanged "
              "except for configurable paths. Section 10 is new: it imports the production code in "
              "`iv_retention/pipeline.py` and proves it gives the same answers as this notebook.")

# --- 2. Setup: configurable paths + import the package ----------------------------
setup = nb.cells[2]
setup.source = setup.source.replace(
    '# --- Paths -----------------------------------------------------------------\n'
    'DATA_PATH = Path("Immaculate_Vibes_Sales.csv")',
    '# --- Paths (deployment: configurable, never hard-coded) ----------------------\n'
    'import os, sys\n'
    'PROJECT_ROOT = Path.cwd() if (Path.cwd() / "iv_retention").exists() else Path.cwd().parent\n'
    'sys.path.insert(0, str(PROJECT_ROOT))\n'
    '_candidates = [os.environ.get("IV_DATA_PATH", ""),\n'
    '               PROJECT_ROOT / "data" / "Immaculate_Vibes_Sales.csv",\n'
    '               Path("Immaculate_Vibes_Sales.csv"),\n'
    '               PROJECT_ROOT / "sample_data" / "sample_sales.csv"]   # de-identified fallback\n'
    'DATA_PATH = next(Path(p) for p in _candidates if p and Path(p).exists())\n'
    'OUT_DIR = PROJECT_ROOT / "outputs"; OUT_DIR.mkdir(exist_ok=True)\n'
    'print("Using data file:", DATA_PATH)')
assert "IV_DATA_PATH" in setup.source

# --- 3. Small fixes found during review ------------------------------------------
for c in nb.cells:
    c.source = c.source.replace('Saved winback_list_lapsed_repeat_customers.csv', 'Saved winback_list_repeat_customers.csv')
    if c.cell_type == "markdown" and c.source.startswith("### 9.2 Files created by this notebook"):
        c.source = c.source.replace("### 9.2 Files created", "### 9.4 Files created")
    if c.cell_type == "code":
        c.source = (c.source.replace('to_csv("rfm_segments.csv"', 'to_csv(OUT_DIR / "rfm_segments.csv"')
                             .replace('to_csv("winback_list_repeat_customers.csv"',
                                      'to_csv(OUT_DIR / "winback_list_repeat_customers.csv"'))
    if c.cell_type == "markdown" and c.source.startswith("### 9.3 Limitations"):
        c.source = c.source.replace("### 9.3", "### 9.3")

# --- 4. Section 10: deployment ---------------------------------------------------
new = [
md("""
---
## 10. Deployment Phase – from analysis to a weekly Retention Action Engine

**Why a deployment phase?** Sections 1–9 answered *"what is happening?"* once. The strategy report's quick wins
(reorder nudges at days 30/60/90, a worth-first win-back list, measuring against a control group) only pay off if
somebody gets the right list **every week** without opening this notebook. So the analysis rules have been moved
into a small, tested Python package and two front ends:

| Notebook piece | Where it lives now | Who uses it |
|---|---|---|
| Load + quality checks (2.1–2.4) | `pipeline.load_orders`, `pipeline.quality_report` – a bad export is **stopped** before any list is produced | Automatic |
| Customer table (2.7) | `pipeline.build_customers` | Automatic |
| RFM + segment rules (4.1–4.3) | `pipeline.score_rfm`, `pipeline.assign_segment` (same rule order: *At Risk* before *Loyal*) | Sales champion |
| Repurchase gaps, drift/lapse (8.3–8.4) | `pipeline.thresholds`, `pipeline.lifecycle` (P75 / P90 recomputed on every run) | Sales champion |
| Recommendations 1 & 3 (9.2) | `pipeline.build_action_list` – nudge + win-back list with a 20% random **hold-out** group | Staff, weekly |
| Report Table 4 targets | `pipeline.compute_kpis` | Owner, weekly |
| Report §4.3 "does it pay?" | `pipeline.roi_scenario`, `pipeline.sensitivity_grid` | Owner |
| — | `run_pipeline.py` (batch job, scheduled weekly by GitHub Actions) and `app.py` (Streamlit app) | Everyone |

The design follows the report's choices: **batch, not real-time** (repurchase gaps are measured in months), **simple
explainable rules before models** (small data), **privacy by design** (pseudonymous IDs, consent flag, real data
never in git).
"""),
md("""
### 10.1 Parity check: does the production code give the same answers as this notebook?

Before anything is deployed, the package is run on the same file and its results are compared with the variables
computed above. If any `assert` fails, the deployment is not trustworthy and must not be used.
"""),
code("""
from iv_retention import pipeline as ivp

prod = ivp.run(ivp.load_orders(DATA_PATH))

checks = {
    "Snapshot date":            (prod.snapshot == snapshot_date),
    "Customers scored (RFM)":   (len(prod.rfm) == len(rfm)),
    "Segment sizes":            prod.segments["customers"].astype(int).equals(seg["customers"].astype(int)),
    "Customer-level segment":   (prod.rfm.set_index("customer_id")["Segment"]
                                 .equals(rfm.set_index("customer_id")["Segment"])),
    "Drift threshold (days)":   np.isclose(prod.drift_days, drift_threshold),
    "Lapse threshold (days)":   np.isclose(prod.lapse_days, churn_threshold),
    "Win-back list members":    set(prod.lifecycle.query("status in ['Drifting','Lapsed']").index) == set(winback["customer_id"]),
    "Win-back past revenue":    np.isclose(prod.kpis["winback_pool_revenue"], winback_rev),
    "Repeat rate":              np.isclose(prod.kpis["repeat_rate"], repeat_rate),
    "2nd order within 90 days": np.isclose(prod.kpis["second_order_90d"], conv.loc[90, "placed_2nd_order"]),
}
parity = pd.Series(checks, name="matches notebook").to_frame()
display(parity)
assert parity["matches notebook"].all(), "Production code disagrees with the notebook – do not deploy"
print("✓ Production pipeline reproduces the notebook exactly.")
"""),
md("""
### 10.2 Validation: does the drift/lapse rule pick the right people? (back-test against a baseline)

The win-back list uses "days since last order" to decide who to contact. A list is only useful if it points
effort at customers who would **not come back on their own** – otherwise we pay for perks given to people who were
going to reorder anyway.

**How the test works (no information leakage):** freeze the data at a past date, compute the thresholds and statuses
using *only* orders before that date, then look 180 days ahead to see who actually came back, with no messaging in
between (none was being sent then). We repeat this at five dates and pool the results, and compare the deployed rule
against two baselines: *"message every repeat customer"* and the blueprint's naive *"silent for more than 14 days"*.
"""),
code("""
AS_OF = ["2025-09-01", "2025-11-01", "2026-01-01", "2026-03-01", "2026-05-01"]
pooled, per_date = ivp.rolling_backtest(df, AS_OF, horizon_days=180)

display(pooled.rename(columns={"flagged": "customer-snapshots flagged",
                               "avg_share_flagged": "avg share of regulars flagged",
                               "returned_unaided_rate": "came back unaided within 180d"})
        .style.format({"avg share of regulars flagged": "{:.0%}", "came back unaided within 180d": "{:.0%}",
                       "ci_low": "{:.0%}", "ci_high": "{:.0%}", "returned": "{:,.0f}"}))

plot = pooled.iloc[::-1]
fig, ax = plt.subplots(figsize=(10, 4))
ax.barh(plot["rule"], plot["returned_unaided_rate"], color=BRAND, height=0.55)
ax.errorbar(plot["returned_unaided_rate"], plot["rule"],
            xerr=[plot["returned_unaided_rate"] - plot["ci_low"], plot["ci_high"] - plot["returned_unaided_rate"]],
            fmt="none", ecolor="#333", capsize=3, lw=1)
ax.axvline(pooled.loc[0, "returned_unaided_rate"], ls="--", color="grey", lw=1)
ax.xaxis.set_major_formatter(mtick.PercentFormatter(1.0)); ax.set_xlim(0, 1)
ax.set_xlabel("Share who came back within 180 days with no message (95% interval)")
ax.set_title("About half of regulars return on their own, whatever rule flags them")
save_fig("10_backtest_rules")
"""),
md("""
> ### What the back-test tells us (and how it changed the deployment)
> * **"Days silent" does not predict who will stay away.** Across five back-test dates, roughly **55%** of repeat
>   customers came back within six months *without any message*, and the rate is similar whichever rule flagged
>   them. *Drifting* customers came back a little less often (≈47%) and *Lapsed* a little more (≈62%), but the
>   intervals overlap with the baseline, so we cannot claim the rule separates "leavers" from "stayers".
> * **The naive 14-day rule is useless as a list**: it flags ~86% of all regulars – it is just "everyone".
> * **What the deployed rule *does* do is focus effort.** Drifting + Lapsed is about a quarter of regulars, ordered
>   by past value, which is a list two people can actually work through in a week. That is a workload decision, not
>   a prediction.
> * **Consequence for deployment:** because about half would return anyway, the only honest way to know whether
>   messages work is to compare messaged customers with a **random hold-out group** that is not messaged. The
>   pipeline therefore holds back 20% of every list (deterministically, by a hash of the customer ID), and the app's
>   *"Did it work?"* tab measures uplift = messaged return rate − hold-out return rate.
> * **Why no churn model (yet):** a machine-learning model would need a signal that "days silent" doesn't carry, and
>   with ~330 regulars there is too little data to find one reliably. Per the report's Phase 3, revisit once the
>   hold-out results and more history exist. *Caveat:* the five snapshots overlap (the same customer can appear in
>   several), so the intervals are slightly narrower than they should be.
"""),
md("""
### 10.3 Is a win-back campaign worth it? Scenario analysis with sensitivity

The benefit depends on things we have not observed yet, so this is a **forecast range, not a result**. Two
assumptions matter most: the **uplift** (extra customers who return *because of* the message, above the ~55% who
return anyway) and the **gross margin**. Cost per contact (message + a free-delivery perk) is the third.
"""),
code("""
n_contact = int(((prod.actions["action"].str.startswith("Win-back")) & (prod.actions["group"] == "Message")).sum())
aov_d = delivered["net_revenue"].mean()
gm = prod.kpis["gross_margin"]
base = ivp.roi_scenario(n_contact, response_rate=0.10, orders_per_win=2, aov=aov_d, margin=gm,
                        cost_per_contact=150, fixed_cost=5000)
print(f"Messaged: {n_contact} regulars | AOV KES {aov_d:,.0f} | gross margin {gm:.0%}")
print(f"Central case (10% uplift, 2 orders each): {base['customers_won']:.0f} customers won back, "
      f"KES {base['extra_revenue']:,.0f} extra revenue, KES {base['extra_gross_profit']:,.0f} gross profit, "
      f"net benefit KES {base['net_benefit']:,.0f}")
print(f"Break-even uplift: {base['break_even_response_rate']:.1%} "
      f"(≈{base['break_even_response_rate'] * n_contact:.1f} extra customers)")

grid = ivp.sensitivity_grid(n_contact, aov_d, 150, 5000, [0.02, 0.05, 0.10, 0.15, 0.20], [0.40, 0.45, 0.49, 0.55])
print("\\nNet benefit (KES) – rows: gross margin, columns: uplift")
display(grid.style.format("{:,.0f}").format_index("{:.0%}", axis=0).format_index("{:.0%}", axis=1)
        .background_gradient(cmap="Purples"))

cost_sens = pd.DataFrame({c: ivp.roi_scenario(n_contact, 0.05, 2, aov_d, gm, c, 5000) for c in [50, 150, 300, 500]}).T
print("Cost per contact sensitivity at a cautious 5% uplift:")
display(cost_sens[["cost", "extra_gross_profit", "net_benefit", "break_even_response_rate"]]
        .style.format({"cost": "{:,.0f}", "extra_gross_profit": "{:,.0f}", "net_benefit": "{:,.0f}",
                       "break_even_response_rate": "{:.1%}"}))
"""),
md("""
> **Reading the scenarios.** At KES 150 per contact the campaign needs an uplift of about **6 percentage points** to
> break even (roughly 7 extra regulars out of ~110 messaged); the central 10% case gives a modest net gain of about
> KES 13,000 in gross profit. **The perk cost is the lever the owner controls:** at KES 50 per contact (a message plus
> a discount only paid when redeemed) break-even drops to about 3 points. So the advice is: start with cheap,
> redeem-only perks, and let the hold-out comparison show the real uplift within 2–3 months before spending more. These numbers are **gross profit, not cash**: deposits arrive with the order, but the perk cost is paid
> on redemption, and none of this covers the report's KES 350k 24-month analytics budget on its own.
"""),
md("""
### 10.4 This week's action list (the deployed output)

This is what staff receive every Monday – the same table the app shows and the batch job writes to
`outputs/action_list_<date>.csv`.
"""),
code("""
acts = prod.actions
print(acts.groupby(["action", "group"]).size().unstack(fill_value=0))
print(f"\\nTo message this week: {(acts['group'] == 'Message').sum()} customers "
      f"| hold-out (do not message): {(acts['group'] != 'Message').sum()}")
acts.to_csv(OUT_DIR / f"action_list_{prod.kpis['snapshot_date']}.csv", index=False)
acts.head(10)
"""),
md("""
### 10.5 Monitoring plan and decision gates

| KPI | Definition (computed by `compute_kpis`) | Baseline | Target / threshold | Review | Stop or revise if |
|---|---|---|---|---|---|
| **Primary: 2nd order within 90 days** | Customers whose 1st order was ≥90 days ago and who placed a 2nd within 90 days | 24% | 35% in 24 months; watch the *recent cohort* figure monthly | Monthly, owner | Recent cohort not above 24% after 6 months of nudges |
| Win-back uplift | Return rate of messaged − hold-out, per campaign | Unknown (≈55% return unaided) | > +5 points | Month 3 and 6 | Uplift ≤ 0 at month 6 → stop perks, keep plain messages |
| Win-back rate | Share of the 141 drifting/lapsed regulars who ordered again | 0 of 141 | 15% (≈21) | Monthly | — |
| Cancel + return rate | Non-delivered orders ÷ all orders, last 90 days | 8.7% | 6.5% | Monthly | Rises above 9% for 2 months |
| Opt-outs | STOP replies ÷ messages sent | — | < 5% | Weekly | > 5% → pause and rewrite messages |
| Model health | Drift/lapse thresholds vs last run; data-quality warnings | 209 / 351 days | Change < 20% | Each run | Big jump → check the export before using the list |

Every run appends to `outputs/kpi_history.csv`, so trends are visible without re-running this notebook. The
report's gates still apply: **month 6** – continue only if the list is used weekly and messages beat the hold-out;
**month 12** – continue only if the weekly job runs without manual fixes.
"""),
code("""
# The same KPIs the app and batch job report, from the production code
pd.Series(prod.kpis).to_frame("value")
"""),
md("""
### 10.6 Responsible deployment

* **Data minimisation:** the engine needs only pseudonymous `customer_id`s and order fields. Phone numbers stay in
  WhatsApp Business; staff look a customer up there by ID. No names or numbers enter the app or the repo.
* **Consent (Data Protection Act 2019, s.37):** each action row carries `whatsapp_consent`. Until a consent register
  is uploaded it reads *"Unknown – confirm before messaging"*, and staff are instructed not to send marketing
  messages without consent. Every message includes an opt-out line; STOP replies go into the register the same day.
* **Access and storage:** the real export is git-ignored; the public repo holds only the de-identified
  `sample_data/`. The hosted app is password-protected (`APP_PASSWORD` secret) and keeps uploads only in memory for
  the session. Weekly outputs are kept 30 days as private GitHub Actions artifacts, then deleted.
* **Human in the loop:** the list *suggests*; a person decides whether and what to send. Nothing is sent
  automatically.
* **Known limits that could change the decision:** the rule does not predict churn (10.2); uplift is unmeasured until
  the hold-out results arrive; segment thresholds are conventions; CLV is historical revenue, not profit after
  acquisition cost.

### 10.7 How to run it

```bash
pip install -r requirements.txt
python run_pipeline.py --data data/Immaculate_Vibes_Sales.csv --snapshot today   # weekly batch job
streamlit run app.py                                                           # owner/staff app
pytest -q                                                                      # tests incl. parity
```
See `README.md` for hosting on Streamlit Community Cloud and the GitHub Actions schedule.
"""),
]
nb.cells.extend(new)
nbf.write(nb, dst)
print("wrote", dst, len(nb.cells), "cells")
