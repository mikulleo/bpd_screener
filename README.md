# BPD Watchlist

The team's shared watchlist: lists of names, each automatically classified into a
**Weinstein phase** and checked against the **BPD setup criteria**. Data lives in a database,
so you import once, then just press **Re-evaluate** (or let a nightly job do it).

- **Phase board**: one column per phase (2b+RS+ … 4), best setups first.
- **Table**: every metric, sortable; filters for setup verdict, *phase changed*, *unsure phase*,
  *differs from my phase*.
- **Ticker drawer**: phase with its reasoning, 52-week phase history, charts (price + 10W/30W EMA,
  weekly & daily BBW with CHAN(125), RS vs $SPX with 52W EMA), the 7 setup checks, notes,
  *my phase* override, list membership, links to StockCharts / TradingView.
- **Import** the WL xlsx: sheets become lists (name, industry, signal and review columns become
  metadata), and phase-named sheets set *my phase*, dated from notes such as `20/7 LM: …`.
- **Settings**: thresholds are shared team settings stored in the database.

## Phases

Three lenses (weekly): **10W vs 30W EMA**, **RS** = `ticker:$SPX` vs its 52W EMA, and **BB Width(20,2)**
position in its **CHAN(125)**. The base phase comes from a weekly state machine, because a phase
depends on what came before it:

| From | To | When (defaults) |
|---|---|---|
| 1 / 4 | **2a** | 10W EMA within 1 % of the 30W (or above), rising ≥ 1 % over 3 wks, price above 30W – catches early 2a before the actual cross |
| 2a | **2b** | uptrend has lasted 8 weeks |
| 2a / 2b | **3** | 10W EMA ≥ 1.5 % below the 30W for 2 weeks (a failed young 2a goes back to **1**) |
| 3 | **2b** | 10W back above 30W and rising |
| 3 | **4** | 10W below 30W for 8+ weeks and still a rolling downtrend (10W, 30W and price all falling/below) |
| 4 | **1** | the decline stalls for 3 weeks |
| 1 | **4** | the rolling downtrend resumes for 8 weeks in a row |

Phase 2 is then refined:

- **2b+RS+ / 2b+RS-**: weekly BBW at/near the channel low (lowest position of the last 3 weeks ≤ 15 %);
  RS+ when the RS line is ≥ 8 % above its 52W EMA.
- **2b-AB**: weekly BBW below half of its channel. **2b**: above half.
- **2a+**: RS above its 52W EMA and that EMA rising; otherwise **2a**.

**1 vs 3** is decided by what preceded the range (an uptrend → 3, a decline → 1). When a name
sits in 1 or 3 for 6+ months the drawer says so – that is the case that is often only clear in hindsight.

**Unsure phase** is shown only when the call sits right on a line (BBW at the 2b+ or half-channel
line, RS at the RS+ line, 10W right at the phase-3 line). **Watch** flags warn early, e.g.
*"getting loose: weekly volatility expanding without new highs"* for the 2b → 3 transition.

### Calibration

Defaults were fitted to the 113 dated phase calls in `WL 2026.xlsx`, each evaluated as of its
note date: **61 % exact phase, 73 % same family (1/2a/2b/3/4)**; the 2b group (2b+RS+, 2b+RS-,
2b-AB, 2b) matches 78 %, the weakest are 2a vs 1 and early-3 calls. Every *my phase* change made
in the app is stored with its date and the auto phase at that time (`GET /api/labels`), so the
rules can be re-fitted on a growing set.

## BPD setup checks

| Check | Pass | Borderline |
|---|---|---|
| Base length (weeks since the base pivot) | ≥ 14 | 7–13 |
| Weekly BBW lowest channel position in the base | ≤ 15 % | ≤ 30 % |
| Daily BBW: share of last 30 sessions in bottom half | ≥ 60 % | ≥ 40 % |
| RS line vs its 52-week high | within 5 % | within 10 % |
| Contraction: recent / early-base daily range (or daily BBW in bottom 25 %) | ≤ 0.80× | ≤ 1.00× |
| Overhead: 2-year volume traded above price | ≤ 20 % | ≤ 35 % |
| EPS: last quarter > 0 and ≥ same quarter last year | ≥ 0 % | −5…0 % |

Any fail → **Reject**; any borderline or missing data → **Check manually**; all pass → **Candidate**.

## Run locally

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn screener.server:app --reload --port 8000   # http://127.0.0.1:8000
.venv/bin/python -m pytest -q                                 # SQLite
DATABASE_URL=postgres://user@host/db .venv/bin/python -m pytest -q   # or against Postgres
```

Without `DATABASE_URL` the app uses `data/bpd.db` (SQLite).

## Deploy

Set **`APP_PASSWORD`** (shared team password) and **`DATABASE_URL`** (Postgres) on the host.

### Railway (recommended)

1. Push this folder to GitHub; Railway → *New project* → *Deploy from GitHub repo*.
2. *+ New* → *Database* → *PostgreSQL*. In the app service → *Variables*, add
   `DATABASE_URL = ${{Postgres.DATABASE_URL}}` and `APP_PASSWORD`.
3. *Settings → Networking → Generate domain*. Tables are created on first start.
4. Optional nightly refresh: add a second service from the same repo with start command
   `python -m screener.refresh`, the same variables, and a cron schedule such as `30 22 * * 1-5`
   (UTC, after the US close).

### Vercel

1. Import the repo (framework preset **Other**).
2. Add a Postgres database (Vercel Marketplace → Neon) so `DATABASE_URL` is set, plus `APP_PASSWORD`.
   Without a database the app warns that storage is temporary.
3. `vercel.json` routes `/api/*` to the FastAPI function (60 s limit; the browser evaluates in
   batches of 8, so this is never hit). The nightly job is Railway-only; on Vercel use the button.

## Data and limitations

- Prices and EPS from Yahoo Finance via `yfinance` (free). Yahoo sometimes rate-limits cloud IPs;
  if that happens in production, swap `screener/data.py` for a paid API (FMP, EODHD, Polygon).
- EMAs on weekly closes (Friday bars); your older sheets mention 10/30 *SMA* – EMA is used per the
  phase notes.
- `*.xlsx` and `data/` are git-ignored so watchlists and the local database stay out of the repo.
