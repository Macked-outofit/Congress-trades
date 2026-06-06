# Capitol Trades — Congressional Stock Disclosure Tracker

A free, self-hosted website that tracks stock trades disclosed by members of Congress under the STOCK Act.

**Data sources:**
- Senate: [efts.senate.gov](https://efts.senate.gov) (Electronic Financial Transaction System)
- House: [disclosures.house.gov](https://disclosures.house.gov) (Periodic Transaction Reports)

**Updates:** Every Monday at 08:00 UTC via GitHub Actions.

---

## Repository structure

```
congress-trades/
├── .github/
│   └── workflows/
│       └── fetch-trades.yml   ← GitHub Actions cron job
├── data/
│   └── trades.json            ← Updated every Monday automatically
├── scripts/
│   └── fetch_trades.py        ← Fetch & merge script (stdlib only, no pip)
├── index.html                 ← Frontend (plain HTML + vanilla JS)
└── README.md
```

---

## Setup (5 minutes)

### 1. Create the GitHub repository

```bash
# Create a new repo at github.com, then:
git clone https://github.com/YOUR_USERNAME/congress-trades.git
cd congress-trades

# Copy all project files in, then:
git add .
git commit -m "initial commit"
git push
```

### 2. Enable GitHub Pages

1. Go to your repo → **Settings** → **Pages**
2. Source: **Deploy from a branch**
3. Branch: `main` / root (`/`)
4. Click **Save**

Your site will be live at: `https://YOUR_USERNAME.github.io/congress-trades/`

### 3. Give Actions permission to push

1. Go to **Settings** → **Actions** → **General**
2. Scroll to **Workflow permissions**
3. Select **Read and write permissions**
4. Click **Save**

### 4. Run the fetch script manually (first data load)

1. Go to **Actions** → **Fetch Congressional Trades**
2. Click **Run workflow** → **Run workflow**

This populates `data/trades.json` immediately.  
After that, it runs automatically every Monday morning.

---

## Running locally

The site fetches `data/trades.json` via `fetch()`, which requires a real HTTP server (not `file://`).

```bash
# Python (built-in, no install needed)
python -m http.server 8080
# Then open: http://localhost:8080
```

---

## Filtering & features

| Feature | How |
|---|---|
| Search | Free-text across member name, ticker, asset name, state |
| Filter by chamber | Senate / House |
| Filter by party | D / R / I |
| Filter by trade type | Buy / Sell / Exchange |
| Filter by amount range | Min and max range brackets |
| Group by | Chamber, party, member, amount range, trade type, or state |
| Sort | Click any column header |
| Paginate | 50 / 100 / 250 / 500 rows per page |

---

## Important caveats

- **Amount ranges, not exact figures.** The STOCK Act requires members to disclose trades in ranges (e.g. `$15,001–$50,000`), not exact dollar amounts. This is by law, not a data limitation.
- **Filing lag.** Members have up to 45 days after the trade date to file. "Updated weekly" means new *filings*, not necessarily last week's *trades*.
- **House XML availability.** The House publishes annual bulk XML files. The current and prior year are fetched. Older records are not re-fetched after the initial run (they are already in `trades.json`).
- **No White House data.** Executive branch financial disclosures are published by the OGE but in a different format and cadence. They are not included here.

---

## Customising

**Change the update schedule** — edit the cron line in `.github/workflows/fetch-trades.yml`:
```yaml
- cron: '0 8 * * 1'   # Monday 08:00 UTC
```

**Add more lookback days** — edit `LOOKBACK_DAYS` in `scripts/fetch_trades.py` (default: 14).

**Change page title / branding** — edit `index.html` `<title>` and `.logo` text.

---

## License

Public domain. Data is U.S. government public disclosure data.
