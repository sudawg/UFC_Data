#!/usr/bin/env python3
"""Build the JSON feeds that power the static site in docs/.

Reads the three CSVs the weekly scraper maintains in Data/ and writes four
files to docs/data/:

    fights.json    every fight, with both corners' stats and odds where known
    fighters.json  per-fighter career summary and record
    events.json    per-event summary
    summary.json   aggregate betting stats (consumed by docs/betting.html)

Run from the repo root:  python3 scripts/build_site_data.py

Several corrections are applied here that the upstream R pipeline gets wrong;
each is marked with a FIX comment below. The most important is weight class:
Scraper.R extracts it with str_extract("\\w+weight"), which matches
"Heavyweight" inside "Light Heavyweight", silently merging 758 light
heavyweight bouts into the heavyweight bucket. We re-derive it from the raw
file instead.
"""

import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "Data"
OUT = ROOT / "docs" / "data"

# Longest-first: "light heavyweight" must be tested before "heavyweight",
# otherwise it is swallowed by the substring match. This is the upstream bug.
WEIGHT_CLASSES = [
    ("light heavyweight", "Light Heavyweight"),
    ("heavyweight", "Heavyweight"),
    ("welterweight", "Welterweight"),
    ("middleweight", "Middleweight"),
    ("lightweight", "Lightweight"),
    ("featherweight", "Featherweight"),
    ("bantamweight", "Bantamweight"),
    ("flyweight", "Flyweight"),
    ("strawweight", "Strawweight"),
    ("catch weight", "Catchweight"),
    ("catchweight", "Catchweight"),
    ("open weight", "Open Weight"),
    ("openweight", "Open Weight"),
]


def norm_name(s):
    """Casefold, strip accents and punctuation - for join keys only."""
    s = str(s).strip().lower()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def slugify(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def parse_weight_class(raw_wc):
    """Return (weight_class, gender, title_flag) from the raw bout string.

    title_flag: 0 = normal, 1 = title bout, 2 = interim title bout.
    The raw string is the ONLY place title status survives - the cleaned CSV
    discards it entirely.
    """
    s = str(raw_wc or "")
    low = s.lower()

    gender = "W" if "women" in low else "M"

    if "interim" in low:
        title = 2
    elif "title" in low or "championship" in low:
        title = 1
    else:
        title = 0

    wc = None
    for needle, label in WEIGHT_CLASSES:
        if needle in low:
            wc = label
            break
    if wc is None:
        # The 15 early "UFC 5 Tournament Title Bout" style rows had no weight
        # limit at all.
        wc = "Open Weight"

    return wc, gender, title


def repair_time(t):
    """FIX: 17,334 of 17,358 rows were round-tripped through R's time parser
    into HH:MM:SS, so a 5:00 finish became 05:00:00 and a 12:13 finish became
    12:13:00. The true finish time is the HH:MM portion."""
    s = str(t or "").strip()
    if not s or s.lower() == "nan":
        return None
    m = re.fullmatch(r"(\d{1,2}):(\d{2}):(\d{2})", s)
    if m:
        return "{}:{}".format(int(m.group(1)), m.group(2))
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    if m:
        return "{}:{}".format(int(m.group(1)), m.group(2))
    return s


def implied_prob(odds):
    odds = np.asarray(odds, dtype=float)
    # Guard both branches: np.where evaluates each eagerly, and odds == -100
    # makes the positive branch divide by zero even though it is discarded.
    neg = np.where(odds < 0, odds, -1.0)
    pos = np.where(odds < 0, 1.0, odds)
    return np.where(odds < 0, (-neg) / (-neg + 100), 100 / (pos + 100))


def to_int(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# 1. Load and reshape the cleaned fight table (2 rows per fight -> 1)
# ---------------------------------------------------------------------------

def load_fights():
    fd = pd.read_csv(DATA / "fight_data.csv")
    total_rows = len(fd)

    fd = fd.sort_values("fight_pk")
    fd["side"] = fd.groupby("fight_pk").cumcount()

    stat_cols = [
        "fighter", "res", "kd", "sig_strike_landed", "sig_strike_attempts",
        "strike_landed", "strike_attempts", "td_landed", "td_attempts",
        "sub_attempts", "pass",
    ]
    wide = fd.pivot(index="fight_pk", columns="side", values=stat_cols)
    wide.columns = ["{}_{}".format(c, "a" if s == 0 else "b") for c, s in wide.columns]
    wide = wide.reset_index()

    meta = fd.groupby("fight_pk").agg(
        date=("date", "first"),
        method_clean=("method", "first"),
        round_finished=("round_finished", "first"),
        rounds=("rounds", "first"),
    ).reset_index()

    fights = wide.merge(meta, on="fight_pk")
    fights["date"] = pd.to_datetime(fights["date"])
    return fights, total_rows


# ---------------------------------------------------------------------------
# 2. Attach raw-file fields the cleaned CSV throws away
# ---------------------------------------------------------------------------

def attach_raw(fights):
    """Join on (date, unordered fighter pair) - verified 100% coverage.

    Row position does NOT correspond to fight_pk (only ~29% agreement), so a
    real key is required.
    """
    raw = pd.read_csv(DATA / "fight_data_raw.csv")
    raw["rdate"] = pd.to_datetime(raw["date"], format="mixed")
    raw["pair"] = [
        frozenset([norm_name(a), norm_name(b)])
        for a, b in zip(raw["fighter_1_Fighter"], raw["fighter_2_Fighter"])
    ]
    raw["jk"] = list(zip(raw["rdate"], raw["pair"]))
    # Sakuraba vs Silveira fought twice on 1997-12-21 (tournament bout then
    # the final). Keep the first occurrence; the pair is otherwise unique.
    raw = raw.drop_duplicates(subset="jk", keep="first")

    fights["pair"] = [
        frozenset([norm_name(a), norm_name(b)])
        for a, b in zip(fights["fighter_a"], fights["fighter_b"])
    ]
    fights["jk"] = list(zip(fights["date"], fights["pair"]))

    keep = raw[[
        "jk", "cards", "fights", "method", "time", "time_format", "referee",
        "weight_class", "fighter_1_Fighter",
    ]].rename(columns={
        "cards": "event_url",
        "fights": "fight_url",
        "method": "method_full",
        "weight_class": "weight_class_raw",
        "fighter_1_Fighter": "raw_first_corner",
    })

    merged = fights.merge(keep, on="jk", how="left")
    unmatched = merged["event_url"].isna().sum()
    if unmatched:
        print("  WARNING: {} fights did not match the raw file".format(unmatched))

    parsed = merged["weight_class_raw"].apply(parse_weight_class)
    merged["weight_class"] = [p[0] for p in parsed]
    merged["gender"] = [p[1] for p in parsed]
    merged["title"] = [p[2] for p in parsed]
    merged["time_fixed"] = merged["time"].apply(repair_time)
    return merged


# ---------------------------------------------------------------------------
# 3. Moneyline join
# ---------------------------------------------------------------------------

def load_moneyline():
    ml = pd.read_csv(DATA / "moneyline.csv")
    # Two pseudo-cards hold speculative matchups rather than real events. The
    # "Potential Fights" one carries a Dec 31 2023 date that would otherwise
    # stretch the reported odds window past the true last event (Aug 19 2023).
    ml = ml[ml["Card"] != "Future Events"].copy()
    ml = ml[~ml["Card"].str.startswith("Potential Fights", na=False)].copy()

    def split_card(card):
        toks = str(card).split(" ")
        if len(toks) < 3:
            return pd.Series({"event": card, "date_str": None})
        return pd.Series({
            "event": " ".join(toks[:-3]),
            "date_str": " ".join(toks[-3:]),
        })

    ml = pd.concat([ml, ml["Card"].apply(split_card)], axis=1)

    def parse_date(s):
        if s is None:
            return pd.NaT
        s2 = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", str(s))
        try:
            return pd.to_datetime(s2, format="%b %d %Y")
        except Exception:
            return pd.NaT

    ml["date"] = ml["date_str"].apply(parse_date)
    ml["fighter_a_odds"] = pd.to_numeric(ml["fighter_a_odds"], errors="coerce")
    ml["fighter_b_odds"] = pd.to_numeric(ml["fighter_b_odds"], errors="coerce")
    ml["fa_n"] = ml["fighter_a"].map(norm_name)
    ml["fb_n"] = ml["fighter_b"].map(norm_name)

    # 246 (Card, a, b) keys appear twice with different prices - scrape
    # snapshots, not genuine line movement. Average them.
    ml = ml.groupby(["Card", "fa_n", "fb_n", "date", "event"], as_index=False).agg(
        fighter_a_odds=("fighter_a_odds", "mean"),
        fighter_b_odds=("fighter_b_odds", "mean"),
    )
    return ml


def join_moneyline(fights, ml):
    fights["fa_n"] = fights["fighter_a"].map(norm_name)
    fights["fb_n"] = fights["fighter_b"].map(norm_name)

    # Every fight is scraped in both orientations, so try both.
    m1 = fights.merge(ml, left_on=["date", "fa_n", "fb_n"],
                      right_on=["date", "fa_n", "fb_n"])
    m1 = m1.assign(oa=m1["fighter_a_odds"], ob=m1["fighter_b_odds"])

    m2 = fights.merge(ml, left_on=["date", "fa_n", "fb_n"],
                      right_on=["date", "fb_n", "fa_n"], suffixes=("", "_ml"))
    m2 = m2.assign(oa=m2["fighter_b_odds"], ob=m2["fighter_a_odds"])

    cols = ["fight_pk", "oa", "ob", "Card"]
    both = pd.concat([m1[cols], m2[cols]], ignore_index=True)
    both = both.drop_duplicates(subset=["fight_pk", "oa", "ob"])

    final = both.groupby("fight_pk", as_index=False).agg(
        oa=("oa", "mean"), ob=("ob", "mean"), card_name=("Card", "first"),
    )

    out = fights.merge(final, on="fight_pk", how="left")
    # |odds| < 100 is impossible in American format - an artifact of averaging
    # conflicting duplicate scrapes.
    bad = (out["oa"].abs() < 100) | (out["ob"].abs() < 100)
    out.loc[bad, ["oa", "ob"]] = np.nan
    return out, int(bad.sum())


# ---------------------------------------------------------------------------
# 4. Assemble entities
# ---------------------------------------------------------------------------

def build(fights):
    fights = fights.sort_values(["date", "fight_pk"]).reset_index(drop=True)

    # Event identity is the card URL, not the date - 5 dates host two events.
    fights["event_key"] = fights["event_url"].fillna(
        "date:" + fights["date"].dt.strftime("%Y-%m-%d")
    )

    # Bout order: within a card, descending fight_pk is main-event-first
    # (verified 99.1% against 5-round bouts).
    fights["bout_order"] = fights.groupby("event_key")["fight_pk"].rank(
        ascending=False, method="first"
    ).astype(int)

    # ---- fighters ----
    names = pd.unique(pd.concat([fights["fighter_a"], fights["fighter_b"]]))
    names = sorted(names)
    fid = {n: i for i, n in enumerate(names)}

    # ---- referees ----
    refs = sorted(r for r in fights["referee"].dropna().unique())
    rid = {r: i for i, r in enumerate(refs)}

    # ---- events ----
    ev_rows = []
    ev_id = {}
    for key, grp in fights.groupby("event_key", sort=False):
        grp = grp.sort_values("bout_order")
        main = grp.iloc[0]
        name = main.get("card_name")
        if isinstance(name, str) and name.strip():
            # Strip the trailing " Mar 6th 2021" the moneyline file appends.
            name = re.sub(r"\s+\w{3}\s+\d{1,2}(st|nd|rd|th)\s+\d{4}$", "", name).strip()
        else:
            name = "{} vs {}".format(
                str(main["fighter_a"]).split()[-1], str(main["fighter_b"]).split()[-1]
            )
        ev_id[key] = len(ev_rows)
        ev_rows.append({
            "i": len(ev_rows),
            "n": name,
            "d": main["date"].strftime("%Y-%m-%d"),
            "nf": int(len(grp)),
            "s": slugify(name),
        })

    # ---- fights ----
    f_rows = []
    for r in fights.itertuples(index=False):
        res_a = str(r.res_a)
        if res_a == "W":
            w = "a"
        elif res_a == "L":
            w = "b"
        elif res_a == "N" or str(r.res_b) == "N":
            w = "N"
        else:
            w = "D"

        oa = None if pd.isna(r.oa) else int(round(r.oa))
        ob = None if pd.isna(r.ob) else int(round(r.ob))

        method = r.method_full if isinstance(r.method_full, str) else r.method_clean

        f_rows.append({
            "i": int(r.fight_pk),
            "d": r.date.strftime("%Y-%m-%d"),
            "e": ev_id[r.event_key],
            "o": int(r.bout_order),
            "a": fid[r.fighter_a],
            "b": fid[r.fighter_b],
            "oa": oa,
            "ob": ob,
            "w": w,
            "m": method,
            "wc": r.weight_class,
            "g": r.gender,
            "t": int(r.title),
            "r": to_int(r.round_finished),
            "tm": r.time_fixed,
            "sr": to_int(r.rounds),
            "ref": rid.get(r.referee) if isinstance(r.referee, str) else None,
            # [kd, sigL, sigA, strL, strA, tdL, tdA, sub, rev] per corner.
            # NB: the upstream "pass" column is really reversals - Scraper.R
            # mislabels the last two totals columns and drops control time.
            "sa": [to_int(r.kd_a), to_int(r.sig_strike_landed_a), to_int(r.sig_strike_attempts_a),
                   to_int(r.strike_landed_a), to_int(r.strike_attempts_a),
                   to_int(r.td_landed_a), to_int(r.td_attempts_a),
                   to_int(r.sub_attempts_a), to_int(getattr(r, "pass_a"))],
            "sb": [to_int(r.kd_b), to_int(r.sig_strike_landed_b), to_int(r.sig_strike_attempts_b),
                   to_int(r.strike_landed_b), to_int(r.strike_attempts_b),
                   to_int(r.td_landed_b), to_int(r.td_attempts_b),
                   to_int(r.sub_attempts_b), to_int(getattr(r, "pass_b"))],
        })

    # ---- fighter aggregates ----
    agg = defaultdict(lambda: {
        "w": 0, "l": 0, "d": 0, "nc": 0, "n": 0,
        "ko": 0, "sub": 0, "dec": 0, "kol": 0, "subl": 0, "decl": 0,
        "kd": 0, "sigL": 0, "sigA": 0, "tdL": 0, "tdA": 0, "sa": 0,
        "first": None, "last": None, "favW": 0, "favN": 0, "dogW": 0, "dogN": 0,
    })

    for f in f_rows:
        for side, other in (("a", "b"), ("b", "a")):
            i = f[side]
            st = f["sa" if side == "a" else "sb"]
            A = agg[i]
            A["n"] += 1
            A["kd"] += st[0]
            A["sigL"] += st[1]
            A["sigA"] += st[2]
            A["tdL"] += st[5]
            A["tdA"] += st[6]
            A["sa"] += st[7]
            if A["first"] is None or f["d"] < A["first"]:
                A["first"] = f["d"]
            if A["last"] is None or f["d"] > A["last"]:
                A["last"] = f["d"]

            m = (f["m"] or "").lower()
            if f["w"] == side:
                A["w"] += 1
                if "ko" in m or "tko" in m:
                    A["ko"] += 1
                elif "sub" in m:
                    A["sub"] += 1
                elif "decision" in m:
                    A["dec"] += 1
            elif f["w"] == other:
                A["l"] += 1
                if "ko" in m or "tko" in m:
                    A["kol"] += 1
                elif "sub" in m:
                    A["subl"] += 1
                elif "decision" in m:
                    A["decl"] += 1
            elif f["w"] == "N":
                A["nc"] += 1
            else:
                A["d"] += 1

            # market record
            my, opp = (f["oa"], f["ob"]) if side == "a" else (f["ob"], f["oa"])
            if my is not None and opp is not None and my != opp and f["w"] in ("a", "b"):
                if my < opp:
                    A["favN"] += 1
                    if f["w"] == side:
                        A["favW"] += 1
                else:
                    A["dogN"] += 1
                    if f["w"] == side:
                        A["dogW"] += 1

    ft_rows = []
    for name, i in fid.items():
        A = agg[i]
        ft_rows.append({
            "i": i, "n": name, "s": slugify(name),
            "w": A["w"], "l": A["l"], "d": A["d"], "nc": A["nc"], "nf": A["n"],
            "ko": A["ko"], "sub": A["sub"], "dec": A["dec"],
            "kol": A["kol"], "subl": A["subl"], "decl": A["decl"],
            "kd": A["kd"], "sigL": A["sigL"], "sigA": A["sigA"],
            "tdL": A["tdL"], "tdA": A["tdA"], "sba": A["sa"],
            "f": A["first"], "la": A["last"],
            "favW": A["favW"], "favN": A["favN"],
            "dogW": A["dogW"], "dogN": A["dogN"],
        })
    ft_rows.sort(key=lambda x: x["i"])

    return f_rows, ft_rows, ev_rows, refs


# ---------------------------------------------------------------------------
# 5. Betting summary (kept compatible with docs/betting.html)
# ---------------------------------------------------------------------------

def build_summary(f_rows, total_fights, ml_min, ml_max):
    priced = [f for f in f_rows if f["oa"] is not None and f["ob"] is not None]
    in_window = [f for f in f_rows if ml_min <= f["d"] <= ml_max]

    rows = []
    for f in priced:
        pa = float(implied_prob(np.array([f["oa"]]))[0])
        pb = float(implied_prob(np.array([f["ob"]]))[0])
        over = pa + pb
        fav = "a" if f["oa"] < f["ob"] else ("b" if f["ob"] < f["oa"] else "pickem")
        decisive = f["w"] in ("a", "b")
        rows.append({
            "d": f["d"], "wc": f["wc"], "g": f["g"], "m": f["m"],
            "fav": fav, "decisive": decisive,
            "fav_won": (f["w"] == fav) if (decisive and fav != "pickem") else None,
            "fav_odds": min(f["oa"], f["ob"]), "dog_odds": max(f["oa"], f["ob"]),
            "fav_p_fair": (pa if fav == "a" else pb) / over,
            "over": over,
            "oa": f["oa"], "ob": f["ob"], "w": f["w"],
        })

    dec = [r for r in rows if r["fav_won"] is not None]
    fav_wr = float(np.mean([r["fav_won"] for r in dec])) if dec else 0.0

    # calibration over per-fighter observations
    obs = []
    for r in rows:
        if not r["decisive"]:
            continue
        pa = float(implied_prob(np.array([r["oa"]]))[0])
        pb = float(implied_prob(np.array([r["ob"]]))[0])
        o = pa + pb
        obs.append((pa / o, r["w"] == "a"))
        obs.append((pb / o, r["w"] == "b"))

    calib = []
    for k in range(10):
        lo, hi = k / 10, (k + 1) / 10
        b = [o for o in obs if (lo < o[0] <= hi) or (k == 0 and o[0] <= hi)]
        calib.append({
            "bucket_lo": -0.001 if k == 0 else lo, "bucket_hi": hi,
            "n": len(b),
            "avg_predicted": float(np.mean([x[0] for x in b])) if b else 0.0,
            "actual_win_rate": float(np.mean([x[1] for x in b])) if b else 0.0,
        })

    band_defs = [
        ("≤ -500", -100000, -500), ("-499 to -300", -499, -300),
        ("-299 to -200", -299, -200), ("-199 to -150", -199, -150),
        ("-149 to -110", -149, -110), ("-109 to +109", -109, 109),
        ("+110 to +149", 110, 149), ("+150 to +249", 150, 249),
        ("≥ +250", 250, 100000),
    ]
    bands = []
    for label, lo, hi in band_defs:
        b = [r for r in dec if lo <= r["fav_odds"] <= hi]
        bands.append({
            "label": label, "lo": lo, "hi": hi, "n": len(b),
            "avg_fav_odds": float(np.mean([r["fav_odds"] for r in b])) if b else 0.0,
            "implied_prob_fair": float(np.mean([r["fav_p_fair"] for r in b])) if b else 0.0,
            "actual_win_rate": float(np.mean([r["fav_won"] for r in b])) if b else 0.0,
        })

    def profit(odds, won):
        if won is None:
            return 0.0
        if not won:
            return -100.0
        return float(odds) if odds > 0 else 10000.0 / abs(odds)

    roi = {}
    for strat in ("always_favorite", "always_underdog"):
        tot, staked, n = 0.0, 0.0, 0
        for r in dec:
            won = r["fav_won"] if strat == "always_favorite" else (not r["fav_won"])
            odds = r["fav_odds"] if strat == "always_favorite" else r["dog_odds"]
            tot += profit(odds, won)
            staked += 100.0
            n += 1
        roi[strat] = {
            "n_fights": n, "total_staked": staked, "total_profit": round(tot, 2),
            "roi_pct": round(100 * tot / staked, 2) if staked else 0.0,
        }

    by_year = []
    years = sorted({r["d"][:4] for r in dec})
    fc = dc = fs = 0.0
    for y in years:
        yr = [r for r in dec if r["d"][:4] == y]
        fp = sum(profit(r["fav_odds"], r["fav_won"]) for r in yr)
        dp = sum(profit(r["dog_odds"], not r["fav_won"]) for r in yr)
        fc += fp
        dc += dp
        fs += 100.0 * len(yr)
        by_year.append({
            "year": int(y), "n": len(yr),
            "fav_profit": round(fp, 2), "dog_profit": round(dp, 2),
            "fav_cum_roi_pct": round(100 * fc / fs, 2) if fs else 0.0,
            "dog_cum_roi_pct": round(100 * dc / fs, 2) if fs else 0.0,
        })

    def group_rate(key):
        out = []
        for g in sorted({r[key] for r in dec}):
            b = [r for r in dec if r[key] == g]
            out.append({key: g, "n": len(b),
                        "favorite_win_rate": round(float(np.mean([r["fav_won"] for r in b])), 4)})
        return sorted(out, key=lambda x: -x["n"])

    wc_rates = [{"weight_class": r["wc"], "n": r["n"],
                 "favorite_win_rate": r["favorite_win_rate"]} for r in group_rate("wc")]
    g_rates = [{"gender": r["g"], "n": r["n"],
                "favorite_win_rate": r["favorite_win_rate"]} for r in group_rate("g")]

    md_fav, md_dog = defaultdict(int), defaultdict(int)
    for r in dec:
        (md_fav if r["fav_won"] else md_dog)[r["m"]] += 1

    dates = [f["d"] for f in priced]
    return {
        "generated": pd.Timestamp.today().strftime("%Y-%m-%d"),
        "overall": {
            "total_fights_in_fight_data": total_fights,
            "fights_in_moneyline_date_window": len(in_window),
            "matched_with_moneyline": len(priced),
            "decisive_matched": sum(1 for r in rows if r["decisive"]),
            "draws": sum(1 for r in rows if r["w"] == "D"),
            "no_contests": sum(1 for r in rows if r["w"] == "N"),
            "pickem_fights": sum(1 for r in rows if r["fav"] == "pickem" and r["decisive"]),
            "favorite_win_rate": round(fav_wr, 4),
            "underdog_win_rate": round(1 - fav_wr, 4),
            "n_favorite_decisions": len(dec),
            "avg_overround": round(float(np.mean([r["over"] for r in rows])), 4) if rows else 0.0,
        },
        "calibration_deciles": calib,
        "favorite_odds_bands": bands,
        "roi_flat_100_stake": roi,
        "roi_by_year": by_year,
        "favorite_win_rate_by_weight_class": wc_rates,
        "favorite_win_rate_by_gender": g_rates,
        "method_distribution_when_favorite_wins": dict(sorted(md_fav.items(), key=lambda x: -x[1])),
        "method_distribution_when_underdog_wins": dict(sorted(md_dog.items(), key=lambda x: -x[1])),
        "date_range": {"min": min(dates), "max": max(dates)},
    }


def write_json(path, obj, indent=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(obj, fh, separators=(",", ":") if indent is None else None,
                  indent=indent, ensure_ascii=False)
    print("  wrote {:<28} {:>9,.0f} KB".format(path.name, path.stat().st_size / 1024))


def main():
    print("Loading fight data...")
    fights, total_rows = load_fights()
    total_fights = len(fights)
    print("  {:,} rows -> {:,} fights".format(total_rows, total_fights))

    print("Attaching raw fields (weight class, referee, title, method)...")
    fights = attach_raw(fights)

    print("Joining moneylines...")
    ml = load_moneyline()
    ml_min = ml["date"].min().strftime("%Y-%m-%d")
    ml_max = ml["date"].max().strftime("%Y-%m-%d")
    fights, n_bad = join_moneyline(fights, ml)
    print("  odds window {} .. {}".format(ml_min, ml_max))
    print("  dropped {} rows with impossible averaged odds".format(n_bad))

    print("Building entities...")
    f_rows, ft_rows, ev_rows, refs = build(fights)
    n_priced = sum(1 for f in f_rows if f["oa"] is not None)
    print("  {:,} fights | {:,} fighters | {:,} events | {:,} referees".format(
        len(f_rows), len(ft_rows), len(ev_rows), len(refs)))
    print("  {:,} fights priced ({:.1%})".format(n_priced, n_priced / len(f_rows)))

    wc_counts = defaultdict(int)
    for f in f_rows:
        wc_counts[f["wc"]] += 1
    print("  weight classes: " + ", ".join(
        "{} {}".format(k, v) for k, v in sorted(wc_counts.items(), key=lambda x: -x[1])))
    assert wc_counts.get("Light Heavyweight", 0) > 700, \
        "Light Heavyweight missing - the upstream weight-class bug has regressed"

    print("Writing JSON...")
    write_json(OUT / "fights.json", {
        "generated": pd.Timestamp.today().strftime("%Y-%m-%d"),
        "count": len(f_rows),
        "referees": refs,
        "fights": f_rows,
    })
    write_json(OUT / "fighters.json", {
        "count": len(ft_rows), "fighters": ft_rows,
    })
    write_json(OUT / "events.json", {
        "count": len(ev_rows), "events": ev_rows,
    })
    write_json(OUT / "summary.json",
               build_summary(f_rows, total_fights, ml_min, ml_max), indent=2)
    print("Done.")


if __name__ == "__main__":
    main()
