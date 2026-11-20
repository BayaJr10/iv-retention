# Immaculate Vibes – Retention Action Engine

A weekly decision aid that turns the sales export into **one list of who to contact
this week** (reorder nudges and win-back messages), checks the retention KPIs against
the targets in the Data Strategy report, and measures whether the messages work.

Built from the `Customer_Retention_Analysis` notebook (CRISP-DM deployment phase).

```
iv_retention_app/
├── iv_retention/pipeline.py      ← all business rules (tested, config-driven)
├── run_pipeline.py               ← weekly batch job (CLI)
├── app.py                        ← Streamlit app for the owner and staff
├── notebooks/Customer_Retention_Analysis_Deployment.ipynb
├── tests/test_pipeline.py        ← parity with notebook + rule tests
├── sample_data/sample_sales.csv  ← de-identified demo data (safe to share)
├── make_sample_data.py           ← regenerates the sample from the real export
├── .github/workflows/weekly.yml  ← Monday-morning scheduled run
├── .streamlit/                   ← theme + secrets template
├── Dockerfile                    ← optional self-hosting
└── data/  outputs/               ← real data and results (git-ignored, never committed)
```

## What the engine decides each week

| Who | Rule | Action |
|---|---|---|
| First-time buyer, 30 / 60 / 90 days after first order | `nudge_days`, 7-day window so a weekly run never skips or repeats | Reorder nudge (thank-you → new collection → small perk) |
| Regular (2+ orders) silent > P75 of repurchase gaps (209 days now) | `drift_quantile = 0.75` | Win-back, priority 1 |
| Regular silent > P90 (351 days now) | `lapse_quantile = 0.90` | Win-back, priority 3 |
| 20% of each list, chosen at random by customer-ID hash | `holdout_share = 0.20` | **Not** messaged – the control group |

Thresholds are recomputed from the data on every run; change them in `DEFAULT_CONFIG`.

## Run it locally

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
mkdir data && cp /path/to/Immaculate_Vibes_Sales.csv data/

pytest -q                                             # 9 tests incl. parity with the notebook
python run_pipeline.py --data data/Immaculate_Vibes_Sales.csv               # as of last order
python run_pipeline.py --data data/Immaculate_Vibes_Sales.csv --snapshot today
streamlit run app.py                                  # opens http://localhost:8501
```

Optional consent register (recommended before real messaging): a CSV with
`customer_id,whatsapp_consent` (`Yes`/`No`). Pass it with `--consent` or upload it in the app.

## Deploy the app (Streamlit Community Cloud – free)

1. **Create a private GitHub repo** and push this folder. `data/` and `outputs/` are in
   `.gitignore`, so the real export is never uploaded. Check with `git status` before the first push.
   ```bash
   git init && git add . && git commit -m "Retention Action Engine v1"
   git branch -M main
   git remote add origin https://github.com/<you>/iv-retention.git
   git push -u origin main
   ```
2. Go to **share.streamlit.io** → sign in with GitHub → **Create app** → pick the repo,
   branch `main`, main file `app.py` → **Deploy**.
3. In the app's **Settings → Secrets**, paste `APP_PASSWORD = "…"` (see `.streamlit/secrets.toml.example`).
   The app then asks for the password before showing anything.
4. Share the URL with the owner. Each Monday they upload the latest export in the sidebar;
   it is processed in memory for that session only and not stored.

The app opens on the de-identified sample until a real file is uploaded, so the public demo exposes no customer data.

**Alternatives:** `docker build -t iv-retention . && docker run -p 8501:8501 iv-retention` on any small VM
(e.g. a Johannesburg-region instance, as in the report's architecture), or Hugging Face Spaces (Streamlit SDK).

## Deploy on Render (free, Docker)

One Docker service serves both parts:

| URL | What |
| --- | --- |
| `https://<name>.onrender.com/` | Landing page (`landing/`, GSAP animations) |
| `https://<name>.onrender.com/app/` | Retention Engine (password protected) |

Inside the container nginx serves the landing page and proxies `/app/` to Streamlit (see `deploy/`).

1. Sign in at **render.com** with GitHub.
2. **New → Blueprint**, pick `BayaJr10/iv-retention`. Render reads `render.yaml` and builds the `Dockerfile`.
3. When asked, enter a strong `APP_PASSWORD`, then **Apply**. The first build takes 5–8 minutes.
4. Open the `onrender.com` link: the landing page appears; **Open the Retention Engine** asks for the password.

Free services sleep after 15 minutes without visitors; the next visit takes about a minute to wake.
Without `APP_PASSWORD` the app refuses to start on Render (it fails closed).

**Landing page credits:** layout adapted from [Lingare](https://github.com/lbegey/tw-lingare-tpl) by Laurent Begey
(MIT, `landing/LICENSE-lingare.txt`); photos from Unsplash (Unsplash Licence); GSAP 3.12.5.
To rebuild the CSS after editing `landing/index.html`: `npx tailwindcss@3 -c landing/tailwind.config.js -o landing/styles.css --minify`
(with an input file containing the three `@tailwind` lines).

## Schedule the weekly batch job (GitHub Actions)

`.github/workflows/weekly.yml` runs every Monday 07:00 Nairobi time (and on every push):
1. runs the tests – no list is produced if they fail;
2. downloads the latest export from the secret `SALES_CSV_URL` (a private direct-download link to the
   sheet / file the owner updates), falling back to the sample if the secret isn't set;
3. runs `run_pipeline.py --snapshot today`;
4. stores `outputs/` as a private artifact for 30 days (repo → Actions → latest run → Artifacts).

Set the secret in **repo → Settings → Secrets and variables → Actions → New repository secret**.
Use **Run workflow** on the Actions tab to test it by hand.

## Weekly routine for the business (staff action guide)

1. Monday: open the app, upload the latest export (or download the Actions artifact).
2. **Data quality** tab: proceed only if it says "No blocking problems".
3. **This week's actions** tab: work top to bottom. Message only rows with consent `Yes`;
   never message hold-out rows. Include the opt-out line; log STOP replies in the consent register.
4. Download the action list and keep it – next month upload it in **Did it work?** to see uplift.
5. Monthly: owner checks the **KPIs** tab against targets (2nd order within 90 days: 24% → 35%;
   cancel+return: 8.7% → 6.5%).

## Validation summary (see notebook §10)

* **Parity:** the package reproduces every notebook figure (736 scored customers, 107 At Risk, 141 on the
  win-back list worth KES 1,266,550, thresholds 209/351 days, 24% second-order rate).
* **Back-test (5 snapshots, 180-day horizon):** ~55% of regulars return unaided whichever rule flags them,
  so "days silent" is a **prioritisation rule, not a churn prediction**. Hence the built-in hold-out group.
* **ROI:** at KES 150 per contact a win-back campaign breaks even at ~6 points of uplift; at KES 50, ~3 points.

## Responsible use

Pseudonymous IDs only; no names or phone numbers in the app or repo. Consent flag on every action row
(Kenya Data Protection Act 2019, s.37). Password-protected app; uploads held in memory only; artifacts
auto-deleted after 30 days. A person decides every message – nothing is sent automatically.
