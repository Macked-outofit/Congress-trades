#!/usr/bin/env python3
"""
fetch_trades.py v3
Source: Financial Modeling Prep (FMP) — both Senate and House
  Senate: https://financialmodelingprep.com/stable/senate-trades
  House:  https://financialmodelingprep.com/stable/house-trades
 
Free tier: 250 calls/day — plenty for a weekly Monday run.
Requires env var: FMP_API_KEY
Get a free key at: https://financialmodelingprep.com/register
"""
 
import json
import os
import re
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
 
# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT      = Path(__file__).parent.parent
DATA_FILE = ROOT / "data" / "trades.json"
DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
 
# ── Config ────────────────────────────────────────────────────────────────────
LOOKBACK_DAYS = 90          # days to look back on each run
REQUEST_DELAY = 1.5         # seconds between API calls (stay well under rate limit)
FMP_API_KEY   = os.environ.get("FMP_API_KEY", "")
FMP_BASE      = "https://financialmodelingprep.com/stable"
 
HEADERS = {
    "User-Agent": "CongressTradesTracker/3.0 (public research; github.com/Macked-outofit/congress-trades)",
    "Accept":     "application/json",
}
 
AMOUNT_RANGES = [
    "$1 - $15,000",
    "$15,001 - $50,000",
    "$50,001 - $100,000",
    "$100,001 - $250,000",
    "$250,001 - $500,000",
    "$500,001 - $1,000,000",
    "$1,000,001 - $5,000,000",
    "$5,000,001 - $25,000,000",
    "$25,000,001 - $50,000,000",
    "Over $50,000,000",
    "Unknown",
]
AMOUNT_ORDER = {r: i for i, r in enumerate(AMOUNT_RANGES)}
 
 
# ── Helpers ───────────────────────────────────────────────────────────────────
def now_utc() -> datetime:
    return datetime.now(timezone.utc)
 
def http_get(url: str) -> list | dict | None:
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        print(f"  HTTP {e.code} → {url}")
        print(f"  Response: {body}")
        return None
    except Exception as e:
        print(f"  Error → {url}: {e}")
        return None
 
def make_id(*parts) -> str:
    return "|".join(str(p).strip().lower() for p in parts)
 
def normalise_amount(raw: str) -> str:
    """Map FMP amount strings to our canonical ranges."""
    if not raw:
        return "Unknown"
    raw = raw.strip()
    # Already in our format
    if raw in AMOUNT_ORDER:
        return raw
    # FMP returns strings like "$1,001 - $15,000" or "$15,001 - $50,000"
    # Normalise by stripping and matching loosely
    cleaned = re.sub(r"[$,\s]", "", raw).lower()
    mapping = {
        "1001-15000":        "$1 - $15,000",
        "1-15000":           "$1 - $15,000",
        "15001-50000":       "$15,001 - $50,000",
        "50001-100000":      "$50,001 - $100,000",
        "100001-250000":     "$100,001 - $250,000",
        "250001-500000":     "$250,001 - $500,000",
        "500001-1000000":    "$500,001 - $1,000,000",
        "1000001-5000000":   "$1,000,001 - $5,000,000",
        "5000001-25000000":  "$5,000,001 - $25,000,000",
        "25000001-50000000": "$25,000,001 - $50,000,000",
    }
    for pat, label in mapping.items():
        lo, hi = pat.split("-")
        if cleaned.startswith(lo) or cleaned == pat:
            return label
    if "over" in cleaned and "50000000" in cleaned:
        return "Over $50,000,000"
    # Return the original if we can't map it — better than losing data
    return raw
 
def parse_date(s: str) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s.strip()[:10], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None
 
def is_recent(date_str: str, since: datetime) -> bool:
    """Return True if date_str is on or after since (or if date is unparseable — include it)."""
    dt = parse_date(date_str)
    if dt is None:
        return True   # unknown date → include rather than silently drop
    return dt >= since
 
 
# ── FMP fetcher (shared logic for Senate + House) ─────────────────────────────
def fetch_fmp(endpoint: str, chamber: str, since: datetime) -> list[dict]:
    """
    Fetch all pages from a FMP congressional trades endpoint.
    endpoint: 'senate-trades' or 'house-trades'
    FMP paginates via ?page=0,1,2,... Each page returns up to 100 records.
    We stop when we get an empty page OR all records are older than `since`.
    """
    if not FMP_API_KEY:
        print(f"[FMP/{chamber}] No FMP_API_KEY set — skipping.")
        return []
 
    trades = []
    page   = 0
    stop   = False
 
    while not stop:
        url  = f"{FMP_BASE}/{endpoint}?page={page}&apikey={FMP_API_KEY}"
        data = http_get(url)
        time.sleep(REQUEST_DELAY)
 
        if not data:
            break
        if isinstance(data, dict):
            # FMP sometimes wraps in {"data": [...]} on error pages
            records = data.get("data") or data.get(endpoint) or []
        else:
            records = data  # plain list
 
        if not records:
            break
 
        for r in records:
            # FMP Senate fields: disclosureDate, transactionDate, senator,
            #   ticker, assetDescription, type, amount, owner, comment
            # FMP House fields:  disclosureDate, transactionDate, representative,
            #   ticker, assetDescription, type, amount, owner
 
            filed_raw = (
                r.get("disclosureDate") or r.get("disclosure_date") or
                r.get("dateRecieved")   or r.get("date_received") or ""
            )
            trade_raw = (
                r.get("transactionDate") or r.get("transaction_date") or
                r.get("tradeDate")       or r.get("trade_date") or ""
            )
 
            # Stop paginating once records are older than our lookback window
            # FMP returns newest-first, so first old record = done
            if filed_raw and not is_recent(filed_raw, since):
                stop = True
                break
 
            member = (
                r.get("senator")        or r.get("representative") or
                r.get("member")         or r.get("name") or "Unknown"
            )
            ticker = (r.get("ticker") or r.get("symbol") or "").upper().strip()
            amount = normalise_amount(
                r.get("amount") or r.get("range") or ""
            )
            tx_type = (
                r.get("type") or r.get("transactionType") or
                r.get("transaction_type") or ""
            )
            asset = (
                r.get("assetDescription") or r.get("asset_description") or
                r.get("asset") or ""
            )
            state = r.get("state") or r.get("stateCode") or ""
            party = r.get("party") or ""
 
            uid = make_id(
                "fmp", chamber, filed_raw, trade_raw,
                member, ticker, tx_type, amount
            )
 
            trades.append({
                "id":               uid,
                "chamber":          chamber,
                "member":           member,
                "state":            state,
                "party":            party,
                "filed_date":       filed_raw,
                "trade_date":       trade_raw,
                "ticker":           ticker,
                "asset_name":       asset,
                "transaction_type": tx_type,
                "amount":           amount,
                "owner":            r.get("owner") or "",
                "source":           f"FMP/{chamber}",
                "filing_id":        str(r.get("id") or r.get("filing_id") or ""),
            })
 
        page += 1
        # Safety: FMP free tier pages 0-24 (2500 records max per endpoint per run)
        # That's 5 pages of 100 for Senate + 5 for House = 10 calls total, well under 250/day
        if page > 24:
            print(f"  [FMP/{chamber}] Reached page limit (25), stopping.")
            break
 
    print(f"[FMP/{chamber}] Collected {len(trades)} records (pages 0–{page-1})")
    return trades
 
 
# ── Merge & save ──────────────────────────────────────────────────────────────
def load_existing() -> dict:
    if DATA_FILE.exists():
        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return {t["id"]: t for t in data.get("trades", [])}
    return {}
 
def save(trades_by_id: dict) -> None:
    all_trades = sorted(
        trades_by_id.values(),
        key=lambda t: (t.get("filed_date", ""), t.get("member", "")),
        reverse=True,
    )
    payload = {
        "meta": {
            "last_updated":  now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "total_records": len(all_trades),
            "sources":       ["Financial Modeling Prep — Senate & House congressional trades"],
            "note":          (
                "Amounts reported as ranges per STOCK Act. "
                "Up to 45-day lag between trade date and filing date."
            ),
        },
        "amount_ranges": AMOUNT_RANGES,
        "trades":        all_trades,
    }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(all_trades)} total records → {DATA_FILE}")
 
 
# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    if not FMP_API_KEY:
        print("ERROR: FMP_API_KEY environment variable is not set.")
        print("Get a free key at https://financialmodelingprep.com/register")
        print("Then add it as a GitHub secret named FMP_API_KEY.")
        raise SystemExit(1)
 
    since = now_utc() - timedelta(days=LOOKBACK_DAYS)
    print(f"Running fetch — lookback {LOOKBACK_DAYS} days (since {since.date()})\n")
 
    existing = load_existing()
    print(f"Existing records: {len(existing)}\n")
 
    new_trades: list[dict] = []
    new_trades += fetch_fmp("senate-trades", "Senate", since)
    new_trades += fetch_fmp("house-trades",  "House",  since)
 
    added = 0
    for t in new_trades:
        if t["id"] not in existing:
            existing[t["id"]] = t
            added += 1
 
    print(f"\nNew records added: {added}")
    save(existing)
 
if __name__ == "__main__":
    main()
 
