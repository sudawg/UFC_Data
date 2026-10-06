#!/usr/bin/env python3
"""
Look up Polymarket price history for a specific UFC fight by Fight ID.

Fight ID = the `fight_pk` column in Data/fight_data.csv (UFCStats fight id).

Pipeline:
  1. Resolve fight_pk -> (fighter_a, fighter_b, date, weight_class) from Data/fight_data.csv
  2. Resolve the Polymarket market for that fight via the Gamma API (cached in
     Data/polymarket_market_map.json so repeat lookups/manual overrides are free)
  3. Pull price history for the market's CLOB token(s) with selectable chart
     time range (--range) and price tick resolution (--tick)
  4. Write a CSV of the series and a self-contained HTML chart

Usage:
  python3 scripts/polymarket_fight_prices.py --fight-id 8679
  python3 scripts/polymarket_fight_prices.py --fight-id 8679 --range 1w --tick 1h
  python3 scripts/polymarket_fight_prices.py --fight-id 8679 --mock   # offline smoke test
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.parse
from datetime import datetime, timezone

import requests

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIGHT_DATA_CSV = os.path.join(REPO_ROOT, "Data", "fight_data.csv")
MARKET_MAP_PATH = os.path.join(REPO_ROOT, "Data", "polymarket_market_map.json")
OUTPUT_DIR = os.path.join(REPO_ROOT, "Data", "polymarket_prices")

GAMMA_SEARCH_URL = "https://gamma-api.polymarket.com/public-search"
CLOB_PRICE_HISTORY_URL = "https://clob.polymarket.com/prices-history"

# --range -> CLOB `interval` param (how far back the chart window goes)
RANGE_CHOICES = {
    "1h": "1h",
    "6h": "6h",
    "1d": "1d",
    "1w": "1w",
    "1m": "1m",
    "all": "all",
    "max": "max",
}

# --tick -> CLOB `fidelity` param in minutes (spacing between price points)
TICK_CHOICES = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "1h": 60,
    "6h": 360,
    "1d": 1440,
}


def load_fight(fight_id):
    """Return list of row dicts from fight_data.csv matching fight_pk == fight_id."""
    rows = []
    with open(FIGHT_DATA_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("fight_pk") == str(fight_id):
                rows.append(row)
    return rows


def load_market_map():
    if os.path.exists(MARKET_MAP_PATH):
        with open(MARKET_MAP_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_market_map(mapping):
    os.makedirs(os.path.dirname(MARKET_MAP_PATH), exist_ok=True)
    with open(MARKET_MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2, sort_keys=True)


def last_name(full_name):
    return full_name.strip().split()[-1].lower() if full_name.strip() else ""


def search_polymarket_market(fighter_a, fighter_b):
    """Query Gamma API public search for a market mentioning both fighters."""
    query = f"{fighter_a} {fighter_b}"
    params = {"q": query, "limit_per_type": 10}
    resp = requests.get(GAMMA_SEARCH_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    candidates = []
    for bucket in ("events", "markets"):
        candidates.extend(data.get(bucket, []) or [])

    la, lb = last_name(fighter_a), last_name(fighter_b)
    best = None
    for item in candidates:
        text = (item.get("title") or item.get("question") or item.get("slug") or "").lower()
        if la in text and lb in text:
            best = item
            break
    return best, candidates


def extract_market_from_event(event):
    """An event from Gamma search may bundle nested markets; find first usable one."""
    markets = event.get("markets") or []
    for m in markets:
        if m.get("clobTokenIds"):
            return m
    if event.get("clobTokenIds"):
        return event
    return None


def resolve_market(fight_id, fighter_a, fighter_b, mapping, force_refresh=False):
    key = str(fight_id)
    if not force_refresh and key in mapping:
        return mapping[key]

    best, _candidates = search_polymarket_market(fighter_a, fighter_b)
    if best is None:
        raise RuntimeError(
            f"No Polymarket market found for '{fighter_a}' vs '{fighter_b}'. "
            f"Add an entry manually to {MARKET_MAP_PATH} with fight_id '{key}', "
            f"e.g. {{'token_ids': ['<clob_token_id>'], 'question': '...'}}"
        )

    market = best
    token_ids = market.get("clobTokenIds")
    if not token_ids:
        market = extract_market_from_event(best) or {}
        token_ids = market.get("clobTokenIds")
    if isinstance(token_ids, str):
        token_ids = json.loads(token_ids)
    if not token_ids:
        raise RuntimeError(
            f"Found a matching market for '{fighter_a}' vs '{fighter_b}' but it has no "
            f"clobTokenIds. Inspect {MARKET_MAP_PATH} / Gamma API response manually."
        )

    outcomes = market.get("outcomes")
    if isinstance(outcomes, str):
        outcomes = json.loads(outcomes)

    entry = {
        "question": market.get("question") or market.get("title"),
        "slug": market.get("slug"),
        "condition_id": market.get("conditionId"),
        "token_ids": token_ids,
        "outcomes": outcomes,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }
    mapping[key] = entry
    save_market_map(mapping)
    return entry


def fetch_price_history(token_id, interval, fidelity_minutes):
    params = {"market": token_id, "interval": interval, "fidelity": fidelity_minutes}
    resp = requests.get(CLOB_PRICE_HISTORY_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("history", [])


def mock_price_history(interval, fidelity_minutes, seed_price=0.5):
    """Synthetic series for offline pipeline testing (no network)."""
    import random

    span_minutes = {
        "1h": 60, "6h": 360, "1d": 1440, "1w": 10080,
        "1m": 43200, "all": 129600, "max": 129600,
    }[interval]
    n_points = max(2, span_minutes // fidelity_minutes)
    now = int(time.time())
    history = []
    price = seed_price
    rng = random.Random(42)
    for i in range(n_points):
        t = now - (n_points - i) * fidelity_minutes * 60
        price = max(0.01, min(0.99, price + rng.uniform(-0.02, 0.02)))
        history.append({"t": t, "p": round(price, 4)})
    return history


def write_csv(history, outcome_label, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp_utc", "unix_ts", "outcome", "price"])
        for point in history:
            ts = datetime.fromtimestamp(point["t"], tz=timezone.utc).isoformat()
            writer.writerow([ts, point["t"], outcome_label, point["p"]])


HTML_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
</head>
<body>
<h2>{title}</h2>
<p>Range: {range_label} &nbsp;|&nbsp; Tick: {tick_label} &nbsp;|&nbsp; Points: {n_points}</p>
<canvas id="chart" width="1100" height="500"></canvas>
<script>
const labels = {labels};
const data = {data};
new Chart(document.getElementById('chart'), {{
  type: 'line',
  data: {{
    labels: labels,
    datasets: [{{
      label: '{outcome_label} price',
      data: data,
      borderColor: '#c0392b',
      borderWidth: 1.5,
      pointRadius: 0,
      tension: 0.1,
    }}]
  }},
  options: {{
    scales: {{
      y: {{ min: 0, max: 1, title: {{ display: true, text: 'Price (probability)' }} }},
      x: {{ ticks: {{ maxTicksLimit: 12 }} }}
    }}
  }}
}});
</script>
</body>
</html>
"""


def write_html(history, outcome_label, title, range_label, tick_label, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    labels = [datetime.fromtimestamp(p["t"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M") for p in history]
    data = [p["p"] for p in history]
    html = HTML_TEMPLATE.format(
        title=title,
        range_label=range_label,
        tick_label=tick_label,
        n_points=len(history),
        labels=json.dumps(labels),
        data=json.dumps(data),
        outcome_label=outcome_label,
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fight-id", required=True, help="fight_pk from Data/fight_data.csv")
    parser.add_argument("--range", choices=sorted(RANGE_CHOICES), default="1w",
                         help="Chart time window to pull (default: 1w)")
    parser.add_argument("--tick", choices=sorted(TICK_CHOICES), default="1h",
                         help="Price tick resolution (default: 1h)")
    parser.add_argument("--outcome", help="Fighter name whose outcome token to chart "
                                           "(default: first fighter found for this fight)")
    parser.add_argument("--refresh-market", action="store_true",
                         help="Re-search Polymarket instead of using the cached market mapping")
    parser.add_argument("--mock", action="store_true",
                         help="Skip network calls and use synthetic data (offline testing)")
    args = parser.parse_args()

    fight_rows = load_fight(args.fight_id)
    if not fight_rows:
        print(f"No fight found in {FIGHT_DATA_CSV} with fight_pk={args.fight_id}", file=sys.stderr)
        sys.exit(1)

    fighters = [r["fighter"] for r in fight_rows]
    date = fight_rows[0]["date"]
    weight_class = fight_rows[0]["weight_class"]
    fighter_a, fighter_b = fighters[0], fighters[1] if len(fighters) > 1 else fighters[0]

    print(f"Fight {args.fight_id}: {fighter_a} vs {fighter_b} ({date}, {weight_class})")

    outcome_label = args.outcome or fighter_a

    if args.mock:
        market = {
            "question": f"Will {fighter_a} beat {fighter_b}?",
            "token_ids": ["MOCK_TOKEN_A", "MOCK_TOKEN_B"],
            "outcomes": [fighter_a, fighter_b],
        }
    else:
        mapping = load_market_map()
        market = resolve_market(args.fight_id, fighter_a, fighter_b, mapping, args.refresh_market)
        print(f"Resolved market: {market.get('question')} (slug={market.get('slug')})")

    token_ids = market["token_ids"]
    outcomes = market.get("outcomes") or [fighter_a, fighter_b]
    try:
        idx = [o.lower() for o in outcomes].index(outcome_label.lower())
    except ValueError:
        idx = 0
    token_id = token_ids[idx]

    interval = RANGE_CHOICES[args.range]
    fidelity = TICK_CHOICES[args.tick]

    if args.mock:
        history = mock_price_history(interval, fidelity)
    else:
        history = fetch_price_history(token_id, interval, fidelity)

    if not history:
        print("No price history returned.", file=sys.stderr)
        sys.exit(1)

    print(f"Fetched {len(history)} price points (range={args.range}, tick={args.tick})")
    first, last = history[0], history[-1]
    print(f"  first: {datetime.fromtimestamp(first['t'], tz=timezone.utc)}  price={first['p']}")
    print(f"  last:  {datetime.fromtimestamp(last['t'], tz=timezone.utc)}  price={last['p']}")

    base = f"{args.fight_id}_{outcome_label.replace(' ', '_')}_{args.range}_{args.tick}"
    csv_path = os.path.join(OUTPUT_DIR, base + ".csv")
    html_path = os.path.join(OUTPUT_DIR, base + ".html")

    write_csv(history, outcome_label, csv_path)
    write_html(history, outcome_label, market.get("question") or f"{fighter_a} vs {fighter_b}",
               args.range, args.tick, html_path)

    print(f"Wrote {csv_path}")
    print(f"Wrote {html_path}")


if __name__ == "__main__":
    main()
