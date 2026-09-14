#!/usr/bin/env python3
"""
Fetches daily market history and writes data.json for the dashboard.
No third-party packages needed (standard library only).

Sources, in order of preference per item:
  yahoo  - Yahoo Finance chart API (prices, futures, ETFs, crypto)
  fred   - St. Louis Fed FRED CSV (Treasury yields; also oil and S&P as backups)
  stooq  - Stooq CSV (backup only)

If every source for an item fails, the previous data.json values for that
item are kept so the dashboard never loses history.
"""
import json, os, sys, datetime as dt, urllib.request, urllib.parse

OUT = "data.json"
YEARS = 10
START = (dt.date.today() - dt.timedelta(days=365 * YEARS)).isoformat()
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"

# id -> list of (source, symbol) to try in order
SERIES = [
    ("wti",    [("yahoo", "CL=F"),    ("fred", "DCOILWTICO")]),
    ("brent",  [("yahoo", "BZ=F"),    ("fred", "DCOILBRENTEU")]),
    ("y1",     [("fred", "DGS1")]),
    ("y5",     [("fred", "DGS5"),     ("yahoo", "^FVX")]),
    ("y10",    [("fred", "DGS10"),    ("yahoo", "^TNX")]),
    ("y30",    [("fred", "DGS30"),    ("yahoo", "^TYX")]),
    ("spx",    [("yahoo", "^GSPC"),   ("fred", "SP500"),   ("stooq", "^spx")]),
    ("vti",    [("yahoo", "VTI"),     ("stooq", "vti.us")]),
    ("vt",     [("yahoo", "VT"),      ("stooq", "vt.us")]),
    ("btc",    [("yahoo", "BTC-USD"), ("stooq", "btc.v")]),
    ("gold",   [("yahoo", "GC=F"),    ("stooq", "xauusd")]),
    ("silver", [("yahoo", "SI=F"),    ("stooq", "xagusd")]),
]


def get(url, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


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
        if v is None:
            continue
        d = dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat()
        pts[d] = float(v)  # last value per day wins
    return sorted(pts.items())


def parse_csv(txt, date_col, val_col):
    lines = [l.strip() for l in txt.splitlines() if l.strip()]
    if not lines or "," not in lines[0]:
        raise ValueError(txt[:120])
    header = [h.strip().lower() for h in lines[0].split(",")]
    di = header.index(date_col) if date_col in header else 0
    vi = header.index(val_col) if val_col in header else 1
    pts = []
    for line in lines[1:]:
        cells = line.split(",")
        if len(cells) <= max(di, vi):
            continue
        d, v = cells[di].strip(), cells[vi].strip()
        if v in (".", "", "null"):
            continue
        try:
            pts.append((d, float(v)))
        except ValueError:
            pass
    return sorted(pts)


def fetch(source, sym):
    if source == "yahoo":
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range={YEARS}y&interval=1d"
        return parse_yahoo(get(url))
    if source == "fred":
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sym}"
        pts = parse_csv(get(url), "observation_date", sym.lower())
        # older FRED exports use header "DATE"
        return pts
    if source == "stooq":
        url = f"https://stooq.com/q/d/l/?s={urllib.parse.quote(sym)}&i=d"
        return parse_csv(get(url), "date", "close")
    raise ValueError(source)


def main():
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
                errs.append(f"{source}:{sym} -> {type(e).__name__}: {str(e)[:100]}")
                print(f"  fail {sid:7s} {errs[-1]}")
        if got is None:
            prev = old.get(sid)
            if prev and prev.get("points"):
                got = {"points": prev["points"], "source": prev.get("source"), "error": "stale: " + " | ".join(errs)}
                print(f"  keep {sid:7s} previous data ({prev['points'][-1][0]})")
            else:
                got = {"points": [], "source": None, "error": " | ".join(errs)}
                failures.append(sid)
        out[sid] = got

    doc = {"generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "series": out}
    with open(OUT, "w") as f:
        json.dump(doc, f, separators=(",", ":"))
    size = os.path.getsize(OUT) / 1024
    print(f"\nWrote {OUT} ({size:.0f} KB). Items with no data at all: {failures or 'none'}")
    # Exit non-zero only if nothing at all could be fetched (so a single flaky source doesn't fail the job).
    if len(failures) == len(SERIES):
        sys.exit(1)


if __name__ == "__main__":
    main()
