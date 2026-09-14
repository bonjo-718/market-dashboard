#!/usr/bin/env python3
"""
Fetches daily market history and writes data.json for the dashboard.
Standard library only.

Primary sources (official APIs, free keys, work from GitHub's servers):
  fred    - FRED API   (needs FRED_API_KEY)        yields, WTI, Brent, S&P 500
  twelve  - Twelve Data (needs TWELVEDATA_API_KEY)  VTI, VT, BTC, gold, silver
Last-resort fallback (often blocked from datacenters, harmless to try):
  yahoo   - Yahoo Finance chart API

Keys are read from environment variables; the GitHub workflow passes them in
from repository secrets. If an item can't be fetched, its previous data.json
values are kept so the dashboard never loses history.
"""
import json, os, sys, time, datetime as dt, urllib.request, urllib.parse

OUT = "data.json"
YEARS = 10
START = (dt.date.today() - dt.timedelta(days=365 * YEARS)).isoformat()
UA = "Mozilla/5.0 (X11; Linux x86_64) market-dashboard/2.0"
FRED_KEY = os.environ.get("FRED_API_KEY", "").strip()
TWELVE_KEY = os.environ.get("TWELVEDATA_API_KEY", "").strip()

# id -> list of (source, symbol) to try in order
SERIES = [
    ("wti",    [("fred", "DCOILWTICO"),   ("yahoo", "CL=F")]),
    ("brent",  [("fred", "DCOILBRENTEU"), ("yahoo", "BZ=F")]),
    ("y1",     [("fred", "DGS1")]),
    ("y5",     [("fred", "DGS5"),         ("yahoo", "^FVX")]),
    ("y10",    [("fred", "DGS10"),        ("yahoo", "^TNX")]),
    ("y30",    [("fred", "DGS30"),        ("yahoo", "^TYX")]),
    ("spx",    [("fred", "SP500"),        ("yahoo", "^GSPC")]),
    ("vti",    [("twelve", "VTI"),        ("yahoo", "VTI")]),
    ("vt",     [("twelve", "VT"),         ("yahoo", "VT")]),
    ("btc",    [("twelve", "BTC/USD"),    ("yahoo", "BTC-USD")]),
    ("gold",   [("twelve", "XAU/USD"),    ("yahoo", "GC=F")]),
    ("silver", [("twelve", "XAG/USD"),    ("yahoo", "SI=F")]),
]


def get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def parse_fred(txt):
    j = json.loads(txt)
    if "observations" not in j:
        raise ValueError(j.get("error_message") or txt[:120])
    pts = []
    for o in j["observations"]:
        v = o.get("value", ".")
        if v in (".", "", None):
            continue
        pts.append((o["date"], float(v)))
    return sorted(pts)


def parse_twelve(txt):
    j = json.loads(txt)
    if j.get("status") == "error" or "values" not in j:
        raise ValueError(j.get("message") or txt[:120])
    pts = {}
    for row in j["values"]:
        d = row["datetime"][:10]
        try:
            pts[d] = float(row["close"])
        except (KeyError, ValueError, TypeError):
            pass
    return sorted(pts.items())


def parse_yahoo(txt):
    j = json.loads(txt)
    res = (j.get("chart") or {}).get("result") or []
    if not res:
        raise ValueError((j.get("chart") or {}).get("error") or "empty result")
    r = res[0]
    ts = r.get("timestamp") or []
    close = r["indicators"]["quote"][0].get("close") or []
    pts = {}
    for t, v in zip(ts, close):
        if v is not None:
            pts[dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat()] = float(v)
    return sorted(pts.items())


def fetch(source, sym):
    if source == "fred":
        if not FRED_KEY:
            raise ValueError("FRED_API_KEY secret not set")
        q = urllib.parse.urlencode({"series_id": sym, "api_key": FRED_KEY, "file_type": "json", "observation_start": START})
        return parse_fred(get("https://api.stlouisfed.org/fred/series/observations?" + q))
    if source == "twelve":
        if not TWELVE_KEY:
            raise ValueError("TWELVEDATA_API_KEY secret not set")
        q = urllib.parse.urlencode({"symbol": sym, "interval": "1day", "outputsize": 5000, "start_date": START,
                                    "apikey": TWELVE_KEY, "format": "JSON"})
        pts = parse_twelve(get("https://api.twelvedata.com/time_series?" + q))
        time.sleep(1.5)  # free plan allows 8 requests/min
        return pts
    if source == "yahoo":
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range={YEARS}y&interval=1d"
        return parse_yahoo(get(url, timeout=15))
    raise ValueError(source)


def main():
    print(f"FRED key: {'set' if FRED_KEY else 'MISSING'} | Twelve Data key: {'set' if TWELVE_KEY else 'MISSING'}\n")
    old = {}
    if os.path.exists(OUT):
        try:
            with open(OUT) as f:
                old = json.load(f).get("series", {})
        except Exception:
            pass

    out, failures = {}, []
    for sid, attempts in SERIES:
        got, errs = None, []
        for source, sym in attempts:
            try:
                pts = [p for p in fetch(source, sym) if p[0] >= START]
                if len(pts) < 20:
                    raise ValueError(f"only {len(pts)} points")
                got = {"points": pts, "source": f"{source}:{sym}", "error": None}
                print(f"  ok   {sid:7s} {source}:{sym:12s} {len(pts):5d} pts, last {pts[-1][0]} = {pts[-1][1]}")
                break
            except Exception as e:
                errs.append(f"{source}:{sym} -> {type(e).__name__}: {str(e)[:120]}")
                print(f"  fail {sid:7s} {errs[-1]}")
        if got is None:
            prev = old.get(sid)
            if prev and prev.get("points"):
                got = {"points": prev["points"], "source": prev.get("source"), "error": "stale: " + " | ".join(errs)}
                print(f"  keep {sid:7s} previous data (through {prev['points'][-1][0]})")
            else:
                got = {"points": [], "source": None, "error": " | ".join(errs)}
                failures.append(sid)
        out[sid] = got

    doc = {"generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "series": out}
    with open(OUT, "w") as f:
        json.dump(doc, f, separators=(",", ":"))
    print(f"\nWrote {OUT} ({os.path.getsize(OUT) / 1024:.0f} KB). Items with no data at all: {failures or 'none'}")
    if len(failures) == len(SERIES):
        sys.exit(1)


if __name__ == "__main__":
    main()
