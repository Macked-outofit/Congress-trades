#!/usr/bin/env python3
"""
fetch_trades.py
Fetches congressional trade disclosures from:
  - Senate EFTS API (efts.senate.gov)
  - House Disclosures (disclosures.house.gov)

Merges new records into data/trades.json without duplicates.
Run every Monday via GitHub Actions cron.
"""

import json
import os
import re
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
DATA_FILE = ROOT / "data" / "trades.json"
DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

# ── Constants ────────────────────────────────────────────────────────────────
# How many days back to look for new filings on each run
LOOKBACK_DAYS = 14          # Monday runs catch anything filed in the past 2 wks
SENATE_PAGE_SIZE = 100
REQUEST_DELAY = 1.0         # seconds between HTTP requests (be polite)

HEADERS = {
    "User-Agent": (
        "CongressTradesTracker/1.0 "
        "(public research tool; github.com/YOUR_USERNAME/congress-trades)"
    ),
    "Accept": "application/json",
}

# Amount-range labels used in disclosures.
# Senate and House use slightly different labels; we normalise to these.
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

# Sort key so ranges appear in ascending order in the UI
AMOUNT_ORDER = {r: i for i, r in enumerate(AMOUNT_RANGES)}


# ── Helpers ───────────────────────────────────────────────────────────────────

def http_get(url: str, params: dict | None = None) -> dict | list | None:
    """Simple urllib GET that returns parsed JSON or None on error."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code} on {url}")
        return None
    except Exception as e:
        print(f"  Error fetching {url}: {e}")
        return None


def normalise_amount(raw: str) -> str:
    """Map various source strings to one of our canonical AMOUNT_RANGES."""
    if not raw:
        return "Unknown"
    raw = raw.strip()

    # Direct match
    if raw in AMOUNT_ORDER:
        return raw

    # Senate uses "1001 - 15000" style integers; House uses dollar strings
    # Strip dollar signs, commas, spaces
    cleaned = re.sub(r"[$,\s]", "", raw)

    mapping = {
        "1-15000":          "$1 - $15,000",
        "15001-50000":      "$15,001 - $50,000",
        "50001-100000":     "$50,001 - $100,000",
        "100001-250000":    "$100,001 - $250,000",
        "250001-500000":    "$250,001 - $500,000",
        "500001-1000000":   "$500,001 - $1,000,000",
        "1000001-5000000":  "$1,000,001 - $5,000,000",
        "5000001-25000000": "$5,000,001 - $25,000,000",
        "25000001-50000000":"$25,000,001 - $50,000,000",
    }
    for pattern, label in mapping.items():
        lo, hi = pattern.split("-")
        if cleaned.startswith(lo) or cleaned == pattern:
            return label

    # Fallback: look for recognisable keywords
    lower = raw.lower()
    if "over 50,000,000" in lower or "over $50" in lower:
        return "Over $50,000,000"

    return raw if raw else "Unknown"


def make_id(*parts) -> str:
    """Build a deduplication key from trade fields."""
    return "|".join(str(p).strip().lower() for p in parts)


# ── Senate EFTS ───────────────────────────────────────────────────────────────

def fetch_senate(since: datetime) -> list[dict]:
    """
    Query the Senate Electronic Financial Transaction System (EFTS) API.
    Docs: https://efts.senate.gov/public/index.cfm/search
    The public search endpoint returns JSON with transaction-level records.
    """
    since_str = since.strftime("%Y-%m-%d")
    today_str = datetime.utcnow().strftime("%Y-%m-%d")

    print(f"[Senate] Fetching filings from {since_str} → {today_str}")

    trades = []
    offset = 0

    while True:
        params = {
            "datePostedStart": since_str,
            "datePostedEnd": today_str,
            "pageSize": SENATE_PAGE_SIZE,
            "offset": offset,
        }
        data = http_get("https://efts.senate.gov/public/index.cfm/filings", params)
        time.sleep(REQUEST_DELAY)

        if not data:
            break

        # Response shape: {"filings": [...], "count": N}
        filings = data.get("filings", []) if isinstance(data, dict) else data
        if not filings:
            break

        for f in filings:
            # Each filing is a disclosure report that may list multiple transactions.
            # We fetch transaction-level data from the individual filing if available.
            fid = f.get("filingID") or f.get("filing_id", "")
            senator = f.get("firstName", "") + " " + f.get("lastName", "")
            senator = senator.strip() or f.get("reporterName", "Unknown")
            state = f.get("stateCode") or f.get("state", "")
            filed_date = f.get("datePosted") or f.get("filed_date", "")

            # Some EFTS responses embed transactions directly
            transactions = f.get("transactions", [])
            if transactions:
                for tx in transactions:
                    trades.append(_senate_tx_to_record(
                        tx, senator, state, filed_date, fid
                    ))
            else:
                # Minimal record from filing header only
                trades.append({
                    "id": make_id("senate", fid),
                    "chamber": "Senate",
                    "member": senator,
                    "state": state,
                    "party": f.get("party", ""),
                    "filed_date": filed_date,
                    "trade_date": "",
                    "ticker": "",
                    "asset_name": f.get("assetName") or f.get("asset_description", ""),
                    "transaction_type": f.get("type") or f.get("transaction_type", ""),
                    "amount": normalise_amount(f.get("amount", "")),
                    "source": "Senate EFTS",
                    "filing_id": str(fid),
                })

        # Pagination
        total = data.get("count", 0) if isinstance(data, dict) else 0
        offset += SENATE_PAGE_SIZE
        if offset >= total or not filings:
            break

    print(f"[Senate] Collected {len(trades)} records")
    return trades


def _senate_tx_to_record(tx: dict, member: str, state: str,
                          filed_date: str, fid: str) -> dict:
    ticker = (tx.get("ticker") or tx.get("tickerSymbol") or "").upper()
    return {
        "id": make_id("senate", fid, tx.get("transactionID", ""), ticker),
        "chamber": "Senate",
        "member": member,
        "state": state,
        "party": tx.get("party", ""),
        "filed_date": filed_date,
        "trade_date": tx.get("transactionDate") or tx.get("trade_date", ""),
        "ticker": ticker,
        "asset_name": tx.get("assetName") or tx.get("asset_description", ""),
        "transaction_type": tx.get("type") or tx.get("transactionType", ""),
        "amount": normalise_amount(tx.get("amount", "")),
        "source": "Senate EFTS",
        "filing_id": str(fid),
    }


# ── House Disclosures ─────────────────────────────────────────────────────────

def fetch_house(since: datetime) -> list[dict]:
    """
    House disclosures are published as XML/JSON data files.
    The easiest machine-readable source is the annual bulk XML:
      https://disclosures.house.gov/public_disc/financial-pdfs/<YEAR>FD.xml
    We also check the ptr (Periodic Transaction Report) feed for recent trades:
      https://disclosures.house.gov/public_disc/ptr-pdfs/<YEAR>FD.xml
    """
    trades = []
    year = datetime.utcnow().year

    # PTR = Periodic Transaction Report (these are the actual stock trades)
    for y in [year - 1, year]:
        url = f"https://disclosures.house.gov/public_disc/ptr-pdfs/{y}FD.xml"
        print(f"[House] Fetching {url}")
        trades += _parse_house_xml(url, since)
        time.sleep(REQUEST_DELAY)

    print(f"[House] Collected {len(trades)} records")
    return trades


def _parse_house_xml(url: str, since: datetime) -> list[dict]:
    """Download and parse a House FD XML file."""
    import xml.etree.ElementTree as ET

    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except Exception as e:
        print(f"  Could not fetch {url}: {e}")
        return []

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        print(f"  XML parse error on {url}: {e}")
        return []

    records = []
    for member in root.iter("Member"):
        rep_name = _xml_text(member, "Name")
        state = _xml_text(member, "StateDst")[:2] if _xml_text(member, "StateDst") else ""
        party = _xml_text(member, "Party")

        for tx in member.iter("Transaction"):
            filed_raw = _xml_text(tx, "FilingDate") or _xml_text(tx, "DateFiled", "")
            filed_dt = _parse_date(filed_raw)
            if filed_dt and filed_dt < since:
                continue  # skip old records

            trade_raw = _xml_text(tx, "TransactionDate") or _xml_text(tx, "Date", "")
            ticker = (_xml_text(tx, "Ticker") or "").upper()
            fid = _xml_text(tx, "DocID") or _xml_text(tx, "FilingID", "")

            records.append({
                "id": make_id("house", fid, trade_raw, ticker, rep_name),
                "chamber": "House",
                "member": rep_name,
                "state": state,
                "party": party,
                "filed_date": filed_raw,
                "trade_date": trade_raw,
                "ticker": ticker,
                "asset_name": _xml_text(tx, "Asset") or _xml_text(tx, "AssetName", ""),
                "transaction_type": _xml_text(tx, "Type") or _xml_text(tx, "TransactionType", ""),
                "amount": normalise_amount(_xml_text(tx, "Amount", "")),
                "source": "House Disclosures",
                "filing_id": str(fid),
            })

    return records


def _xml_text(el, tag: str, default: str = "") -> str:
    child = el.find(tag)
    return child.text.strip() if child is not None and child.text else default


def _parse_date(s: str) -> datetime | None:
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except (ValueError, AttributeError):
            pass
    return None


# ── Merge & save ──────────────────────────────────────────────────────────────

def load_existing() -> dict:
    """Load existing trades.json; return a dict keyed by trade id."""
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
            "last_updated": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "total_records": len(all_trades),
            "sources": ["Senate EFTS (efts.senate.gov)", "House (disclosures.house.gov)"],
            "note": (
                "Trade amounts are reported as ranges per STOCK Act requirements. "
                "Lag between trade date and filing date can be up to 45 days."
            ),
        },
        "amount_ranges": AMOUNT_RANGES,
        "trades": all_trades,
    }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(all_trades)} total records → {DATA_FILE}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    since = datetime.utcnow() - timedelta(days=LOOKBACK_DAYS)
    print(f"Running fetch — looking back {LOOKBACK_DAYS} days (since {since.date()})\n")

    existing = load_existing()
    print(f"Existing records: {len(existing)}\n")

    new_trades: list[dict] = []
    new_trades += fetch_senate(since)
    new_trades += fetch_house(since)

    added = 0
    for t in new_trades:
        if t["id"] not in existing:
            existing[t["id"]] = t
            added += 1

    print(f"\nNew records added: {added}")
    save(existing)


if __name__ == "__main__":
    main()
