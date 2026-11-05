#!/usr/bin/env python3
"""Monitor makro USA -> NQ100 i złoto.
Dane: FRED API (darmowy klucz). Opcjonalnie: yfinance (ceny NQ/złoto), Telegram (alerty).
Uruchomienie:  python makro_monitor.py        (pętla, odświeża co interval_minutes)
               python makro_monitor.py --once (jednorazowo)
Wynik: dashboard.html obok skryptu (sam odświeża się w przeglądarce).
"""
import os, re, sys, csv, json, time, calendar, threading, subprocess, html, urllib.request, urllib.parse
from datetime import datetime, date, timedelta, time as dtime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

BASE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH, STATE_PATH, OUT_PATH = (os.path.join(BASE, f) for f in ("config.json", "state.json", "dashboard.html"))
FC_PATH, JOURNAL_PATH = os.path.join(BASE, "forecasts.json"), os.path.join(BASE, "journal.csv")
LOG_PATH = os.path.join(BASE, "monitor.log")
CFG, LAST = {"port": 8765}, {"rows": [], "px": [], "errors": []}
MEGA = ["NVDA", "MSFT", "AAPL", "AMZN", "META", "GOOGL"]
PAST, RAW, PXV, SP, LEV = {}, {}, {}, {}, {}

NOTES_PATH = os.path.join(BASE, "notes.json")
CLOSED = {"2026-01-01": "Nowy Rok", "2026-01-19": "MLK Day", "2026-02-16": "Presidents Day", "2026-04-03": "Wielki Piątek",
          "2026-05-25": "Memorial Day", "2026-06-19": "Juneteenth", "2026-07-03": "Dzień Niepodległości (obs.)",
          "2026-09-07": "Labor Day", "2026-11-26": "Thanksgiving", "2026-12-25": "Boże Narodzenie"}
EARLY = {"2026-11-27": "Skrócona sesja (do 19:00 PL)", "2026-12-24": "Skrócona sesja (do 19:00 PL)"}
HIGH = {"CPI", "Core CPI", "NFP", "Stopa bezrobocia", "Core PCE", "FOMC (decyzja)"} | {f"{x} (wyniki)" for x in MEGA}
OFFS = (0, 5, 15, 60)
NY, WAW = ZoneInfo("America/New_York"), ZoneInfo("Europe/Warsaw")

# nazwa, series_id, tryb (pct=zmiana % m/m, diff=różnica, level=poziom), znak (+1: wyżej = jastrzębio, -1: wyżej = gołębio),
# skala, jednostka, release_id FRED, godzina publikacji (ET), dzienny?
IND = [
    ("CPI",              "CPIAUCSL",        "pct",   1, 1,    "% m/m",  10,  dtime(8, 30),  False),
    ("Core CPI",         "CPILFESL",        "pct",   1, 1,    "% m/m",  10,  dtime(8, 30),  False),
    ("NFP",              "PAYEMS",          "diff",  1, 1,    "tys.",   50,  dtime(8, 30),  False),
    ("Stopa bezrobocia", "UNRATE",          "level", -1, 1,   "%",      50,  dtime(8, 30),  False),
    ("Core PCE",         "PCEPILFE",        "pct",   1, 1,    "% m/m",  54,  dtime(8, 30),  False),
    ("PPI",              "PPIFIS",          "pct",   1, 1,    "% m/m",  46,  dtime(8, 30),  False),
    ("Sprzedaż detal.",  "RSAFS",           "pct",   1, 1,    "% m/m",  9,   dtime(8, 30),  False),
    ("Wnioski o zasiłek","ICSA",            "level", -1, 1000, "tys.",  180, dtime(8, 30),  False),
    ("PKB (ann.)",       "A191RL1Q225SBEA", "level", 1, 1,    "%",      53,  dtime(8, 30),  False),
    ("JOLTS",            "JTSJOL",          "level", 1, 1,    "tys.",   192, dtime(10, 0),  False),
    ("Rentowność 10Y",   "DGS10",           "level", 1, 1,    "%",      None, None,         True),
    ("Real yield 10Y",   "DFII10",          "level", 1, 1,    "%",      None, None,         True),
    ("Indeks USD (szeroki)", "DTWEXBGS",    "level", 1, 1,    "pkt",    None, None,         True),
    ("Rentowność 2Y",    "DGS2",            "level", 1, 1,    "%",      None, None,         True),
    ("Spread 10Y–2Y",    "T10Y2Y",          "level", 0, 1,    "pp",     None, None,         True),
    ("VIX",              "VIXCLS",          "level", 0, 1,    "pkt",    None, None,         True),
    ("Ropa WTI",         "DCOILWTICO",      "level", 0, 1,    "$/bbl",  None, None,         True),
    ("Inflacja oczek. 10Y", "T10YIE",       "level", 0, 1,    "%",      None, None,         True),
    ("VXN (zmienność NQ)",  "VXNCLS",   "level", 0, 1,    "pkt",    None, None,         True),
    ("GVZ (zmienność złota)", "GVZCLS",  "level", 0, 1,    "pkt",    None, None,         True),
]
FOMC = ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"]
MONTHS = ["", "Styczeń", "Luty", "Marzec", "Kwiecień", "Maj", "Czerwiec", "Lipiec", "Sierpień", "Wrzesień", "Październik", "Listopad", "Grudzień"]
PRICES = {"NQ=F": "NQ100 (futures)", "GC=F": "Złoto (futures)"}


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def config():
    c = {"fred_api_key": "", "telegram_token": "", "telegram_chat_id": "",
         "interval_minutes": 15, "alert_minutes": [60, 15, 5], "desktop_notifications": True, "forecasts": {},
         "news_window": {"before": 5, "after": 3},
         "news_checklist": ["Zamknij lub zabezpiecz pozycje", "Sprawdź spread i płynność", "Nie wchodź przed pierwszą świecą 5-min po publikacji"],
         "custom_events": [], "tv_widgets": True, "tv": {}, "sound_lead_sec": 60,
         "morning_briefing": True, "earnings": {}, "price_alerts": [],
         "evening_summary": True, "evening_time": "22:30", "morning_time": "08:00", "port": 8765}
    c.update(load(CFG_PATH, {}))
    HIGH.update(e["name"] for e in c.get("custom_events", []) if e.get("impact") == "high")
    if os.getenv("GITHUB_ACTIONS"):
        c["desktop_notifications"] = False
    c["fred_api_key"] = os.getenv("FRED_API_KEY", c["fred_api_key"])
    c["telegram_token"] = os.getenv("TELEGRAM_TOKEN", c["telegram_token"])
    c["telegram_chat_id"] = os.getenv("TELEGRAM_CHAT_ID", c["telegram_chat_id"])
    return c


def fred(cfg, path, **p):
    p.update(api_key=cfg["fred_api_key"], file_type="json")
    url = f"https://api.stlouisfed.org/fred/{path}?{urllib.parse.urlencode(p)}"
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.load(r)


def series_m(cfg, ind, limit):
    """Lista (data, wartość) od najnowszej, w jednostkach wskaźnika (np. % m/m)."""
    sid, mode, scale = ind[1], ind[2], ind[4]
    obs = fred(cfg, "series/observations", series_id=sid, sort_order="desc", limit=limit)["observations"]
    v = [(o["date"], float(o["value"])) for o in obs if o["value"] != "."]
    RAW[sid] = v
    if mode == "pct":
        m = [(v[i][1] / v[i + 1][1] - 1) * 100 for i in range(len(v) - 1)]
    elif mode == "diff":
        m = [v[i][1] - v[i + 1][1] for i in range(len(v) - 1)]
    else:
        m = [x[1] / scale for x in v]
    return [(v[i][0], m[i]) for i in range(len(m))]


def metric(cfg, ind):
    sid = ind[1]
    dm = series_m(cfg, ind, 270 if ind[8] else 60)
    m = [x[1] for x in dm]
    n = 250 if ind[8] else 52 if sid == "ICSA" else 5 if sid == "A191RL1Q225SBEA" else 13
    hist = m[:n][::-1]
    if len(hist) > 60:
        hist = hist[::-1][::len(hist) // 60 + 1][::-1]
    v, yoy, yoyp = RAW[sid], None, None
    if ind[2] == "pct" and len(v) > 13:
        yoy, yoyp = (v[0][1] / v[12][1] - 1) * 100, (v[1][1] / v[13][1] - 1) * 100
    return {"date": dm[0][0], "now": m[0], "prev": m[1], "hist": hist, "yoy": yoy, "yoyp": yoyp}


def signal(ind, now, prev, fc):
    sign = ind[3]
    if sign == 0:
        return "none", "–", 0.0
    base = fc if fc is not None else prev
    d = now - base
    if abs(d) < 1e-9:
        return "flat", ("prognoza" if fc is not None else "poprzedni"), d
    return ("hawk" if sign * d > 0 else "dove"), ("prognoza" if fc is not None else "poprzedni"), d


def next_release(cfg, ind):
    rid, t = ind[6], ind[7]
    if rid is None:
        return []
    ds = fred(cfg, "release/dates", release_id=rid, sort_order="desc", limit=30,
              include_release_dates_with_no_data="true")["release_dates"]
    now = datetime.now(WAW)
    cands, past = [], []
    for d in ds:
        dt = datetime.combine(date.fromisoformat(d["date"]), t, NY).astimezone(WAW)
        (cands if dt > now else past).append(dt)
    PAST[ind[1]] = max(past) if past else None
    return sorted(cands)


def prices():
    out = []
    try:
        import yfinance as yf
        for sym, label in PRICES.items():
            h = yf.Ticker(sym).history(period="5d")["Close"].dropna()
            out.append((label, float(h.iloc[-1]), (h.iloc[-1] / h.iloc[-2] - 1) * 100))
            PXV[sym] = (label, float(h.iloc[-1]))
    except Exception:
        pass
    return out


def check_levels(cfg, rows, state):
    vals = {r["sid"]: (r["name"], r["now"]) for r in rows}
    vals.update(PXV)
    day = date.today().isoformat()
    for rule in cfg.get("price_alerts", []):
        name, v = vals.get(rule.get("series"), (None, None))
        if name is None:
            continue
        for k in ("above", "below"):
            t = rule.get(k)
            key = f"{day}|{rule['series']}|{k}|{t}"
            if t is not None and (v > t if k == "above" else v < t) and key not in state["alerted"]:
                notify(cfg, f"🔔 {name}: {v:.2f} ({'powyżej' if k == 'above' else 'poniżej'} {t})")
                state["alerted"] = (state["alerted"] + [key])[-300:]


def levels():
    out = {}
    try:
        import yfinance as yf
    except Exception:
        return out
    today = datetime.now(NY).date()
    for sym in PRICES:
        try:
            h = yf.Ticker(sym).history(period="1mo", interval="1d")[["High", "Low", "Close"]].dropna()
            h.index = [i.date() for i in h.index]
            done = h[h.index < today]
            y = done.iloc[-1]
            yh, yl, yc = float(y.High), float(y.Low), float(y.Close)
            pv = (yh + yl + yc) / 3
            wk = h[h.index >= today - timedelta(days=today.weekday())]
            tr = [max(r.High - r.Low, abs(r.High - c), abs(r.Low - c))
                  for r, c in zip(done.iloc[1:].itertuples(), done.Close.iloc[:-1])][-14:]
            d = {"yh": yh, "yl": yl, "yc": yc, "p": pv, "r1": 2 * pv - yl, "s1": 2 * pv - yh,
                 "r2": pv + (yh - yl), "s2": pv - (yh - yl), "atr": sum(tr) / len(tr),
                 "wh": float(wk.High.max()) if len(wk) else None, "wl": float(wk.Low.min()) if len(wk) else None}
            try:
                m = yf.Ticker(sym).history(period="5d", interval="15m")[["High", "Low"]].dropna()
                now = datetime.now(WAW)
                end = now if now.time() < dtime(15, 30) else now.replace(hour=15, minute=30, second=0, microsecond=0)
                start = (end - timedelta(days=1)).replace(hour=22, minute=0, second=0, microsecond=0)
                seg = m[(m.index >= start) & (m.index <= end)]
                if len(seg):
                    d["oh"], d["ol"] = float(seg.High.max()), float(seg.Low.min())
            except Exception:
                pass
            out[sym] = d
        except Exception:
            pass
    return out


def telegram(cfg, text):
    if not (cfg["telegram_token"] and cfg["telegram_chat_id"]):
        return
    try:
        data = urllib.parse.urlencode({"chat_id": cfg["telegram_chat_id"], "text": text}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{cfg['telegram_token']}/sendMessage", data, timeout=20)
    except Exception as e:
        print("Telegram:", e)


def desktop(title, text):
    q = lambda x: x.replace("'", "''")
    try:
        if sys.platform.startswith("win"):
            ps = ("Add-Type -AssemblyName System.Windows.Forms;$n=New-Object System.Windows.Forms.NotifyIcon;"
                  "$n.Icon=[System.Drawing.SystemIcons]::Information;$n.Visible=$true;"
                  f"$n.ShowBalloonTip(15000,'{q(title)}','{q(text)}',[System.Windows.Forms.ToolTipIcon]::Info);"
                  "Start-Sleep -Seconds 16;$n.Dispose()")
            subprocess.Popen(["powershell", "-NoProfile", "-Command", ps], creationflags=0x08000000)
        elif sys.platform == "darwin":
            subprocess.Popen(["osascript", "-e", f'display notification "{text}" with title "{title}" sound name "Glass"'])
        else:
            subprocess.Popen(["notify-send", title, text])
    except Exception as e:
        print("Powiadomienie na pulpicie:", e)


def notify(cfg, text, title="Makro USA"):
    print("\a" + text)
    telegram(cfg, text)
    if cfg.get("desktop_notifications", True):
        desktop(title, text)


def earnings(cfg, state):
    today = date.today().isoformat()
    c = state.get("earn")
    if not (c and c.get("day") == today and (c["data"] or time.time() - c.get("ts", 0) < 3600)):
        data = {}
        try:
            import yfinance as yf
            for sym in MEGA:
                try:
                    ed = yf.Ticker(sym).calendar.get("Earnings Date") or []
                    ds = [d.date() if isinstance(d, datetime) else d for d in ed]
                    ds = [d for d in ds if d >= date.today()]
                    if ds:
                        data[sym] = min(ds).isoformat()
                except Exception:
                    pass
        except Exception:
            pass
        data.update(cfg.get("earnings", {}))
        c = state["earn"] = {"day": today, "ts": time.time(), "data": data}
    return [(datetime.combine(date.fromisoformat(d), dtime(16, 5), NY).astimezone(WAW), f"{sym} (wyniki)")
            for sym, d in c["data"].items()]


def upcoming(rows, back=0):
    now = datetime.now(WAW)
    g = {}
    for r in rows:
        for dt in r.get("all", []):
            g.setdefault(dt, []).append(r["name"])
        if back and r.get("last"):
            g.setdefault(r["last"], []).append(r["name"])
    for dt, n in LAST.get("earn", []) + custom_events():
        g.setdefault(dt, []).append(n)
    for d in FOMC:
        dt = datetime.combine(date.fromisoformat(d), dtime(14, 0), NY).astimezone(WAW)
        g.setdefault(dt, []).append("FOMC (decyzja)")
    return sorted((dt, n) for dt, n in g.items() if dt > now - timedelta(minutes=back))


def send_alerts(cfg, ev, state):
    now = datetime.now(WAW)
    thr = sorted(cfg["alert_minutes"])
    for dt, names in ev:
        mins = (dt - now).total_seconds() / 60
        ap = [t for t in thr if mins <= t]
        if mins > 0 and ap and f"{dt.isoformat()}|{min(ap)}" not in state["alerted"]:
            notify(cfg, f"⏰ Za {max(1, int(mins))} min: {', '.join(names)} ({dt.strftime('%H:%M')} czasu PL)")
            state["alerted"] = (state["alerted"] + [f"{dt.isoformat()}|{t}" for t in ap])[-300:]


def arrows(sig):
    return {"hawk": ("▼", "▼", "dn"), "dove": ("▲", "▲", "up"), "flat": ("•", "•", "fl"), "none": ("–", "–", "fl")}[sig]


def fmt(x, unit):
    return f"{x:,.2f} {unit}".replace(",", " ")


DNI = ["pn", "wt", "śr", "cz", "pt", "sb", "nd"]

CSS = """
:root{--bg:#f4f6fa;--card:#fff;--tx:#0f172a;--mu:#64748b;--bd:#e2e8f0;--ac:#2563eb;--up:#15803d;--upb:#dcfce7;--dn:#b91c1c;--dnb:#fee2e2;--am:#b45309;--amb:#fef3c7;--nb:#eef2f7;color-scheme:light}
@media(prefers-color-scheme:dark){:root{--bg:#0b0f17;--card:#131a26;--tx:#e6ebf3;--mu:#8b97ab;--bd:#243044;--ac:#60a5fa;--up:#4ade80;--upb:#14301f;--dn:#f87171;--dnb:#3a1717;--am:#fbbf24;--amb:#3a2c0e;--nb:#1b2536;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#0b0f17;--card:#131a26;--tx:#e6ebf3;--mu:#8b97ab;--bd:#243044;--ac:#60a5fa;--up:#4ade80;--upb:#14301f;--dn:#f87171;--dnb:#3a1717;--am:#fbbf24;--amb:#3a2c0e;--nb:#1b2536;color-scheme:dark}
:root[data-theme="light"]{--bg:#f4f6fa;--card:#fff;--tx:#0f172a;--mu:#64748b;--bd:#e2e8f0;--ac:#2563eb;--up:#15803d;--upb:#dcfce7;--dn:#b91c1c;--dnb:#fee2e2;--am:#b45309;--amb:#fef3c7;--nb:#eef2f7;color-scheme:light}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1560px;margin:0 auto;padding:20px 16px 48px}
header{display:flex;justify-content:space-between;align-items:flex-end;gap:12px;flex-wrap:wrap;margin-bottom:16px}
h1{font-size:22px;margin:0;letter-spacing:-.01em}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.07em;color:var(--mu);margin:0 0 10px;font-weight:600}
small{color:var(--mu)}
.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:14px 16px;min-width:0}
.sec{margin-top:14px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
.kpi .lb{color:var(--mu);font-size:12px;margin-bottom:2px}
.kpi .big{font-size:25px;font-weight:650;font-variant-numeric:tabular-nums;margin:2px 0 4px}
.kpi.wide{grid-column:span 2}
@media(max-width:560px){.kpi.wide{grid-column:auto}}
.pill{display:inline-block;padding:2px 9px;border-radius:99px;font-size:12px;font-weight:600;background:var(--nb);color:var(--mu);white-space:nowrap}
.pill.up{background:var(--upb);color:var(--up)}.pill.dn{background:var(--dnb);color:var(--dn)}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:900px){.cols{grid-template-columns:1fr}}
.it{display:grid;grid-template-columns:1fr auto;gap:2px 10px;padding:8px 0;border-top:1px solid var(--bd)}
.it:first-of-type{border-top:0}
.ag{grid-template-columns:12px 1fr auto;align-items:center}
.dot{width:9px;height:9px;border-radius:50%;background:var(--am)}.dot.hi{background:var(--dn)}
.tm{font-weight:600;font-variant-numeric:tabular-nums}
.tb{padding:14px 0 8px}.tb>h2{padding:0 16px}
.r,.rh{display:grid;grid-template-columns:minmax(150px,1.6fr) minmax(120px,1fr) minmax(160px,1.3fr) 124px 104px minmax(120px,1fr);gap:10px;align-items:center;padding:9px 16px}
.r{border-top:1px solid var(--bd)}
.rh{color:var(--mu);font-size:11px;text-transform:uppercase;letter-spacing:.05em;padding-top:2px;padding-bottom:6px}
.r.daily,.rh.daily{grid-template-columns:minmax(150px,1.6fr) minmax(120px,1fr) minmax(160px,1.3fr) 124px}
.n b{display:block}.n small{font-size:12px}
.v{font-size:16px;font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap}.u{color:var(--mu);font-size:12px}
.vv small{display:block;white-space:nowrap;font-size:12px}
.sg{display:flex;gap:5px;flex-wrap:wrap;align-items:center}.sg small{font-size:12px}
.fc{width:92px;padding:5px 7px;border:1px solid var(--bd);border-radius:7px;background:var(--bg);color:var(--tx);font:inherit}
.nt{font-size:13px;white-space:nowrap}
button{padding:7px 14px;border-radius:8px;border:1px solid var(--bd);background:var(--card);color:var(--tx);cursor:pointer;font:inherit}
button:hover{border-color:var(--ac)}
.foot{padding:10px 16px 2px}
@media(max-width:840px){
.rh{display:none}
.r,.r.daily{grid-template-columns:1fr auto;grid-template-areas:"n v" "sg tr" "fc nt";row-gap:6px}
.r.daily{grid-template-areas:"n v" "sg tr"}
.n{grid-area:n}.vv{grid-area:v;text-align:right}.sg{grid-area:sg}.tr{grid-area:tr;justify-self:end}.fcw{grid-area:fc}.nt{grid-area:nt;text-align:right}}
.tbl{width:100%;border-collapse:collapse}
.tbl th,.tbl td{padding:7px 8px;text-align:left;border-top:1px solid var(--bd);font-variant-numeric:tabular-nums}
.tbl th{color:var(--mu);font-size:11px;text-transform:uppercase;letter-spacing:.05em;border-top:0}
.w{overflow-x:auto}
.cal{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:4px;min-width:680px}
.dh{color:var(--mu);font-size:11px;text-align:center;text-transform:uppercase}
.d{background:var(--card);border:1px solid var(--bd);border-radius:8px;min-height:84px;padding:5px;font-size:11px}
.d.o{background:transparent;border-color:transparent}.d.wk{background:var(--nb)}.d.t{outline:2px solid var(--ac);outline-offset:-1px}
.num{display:block;color:var(--mu);font-size:12px;margin-bottom:2px}
.ev{margin-top:3px;padding:2px 5px;border-radius:5px;background:var(--nb);line-height:1.3}
.ev.h,.ev.f{background:var(--dnb);color:var(--dn);font-weight:600}.ev.e{background:var(--amb);color:var(--am)}
.err{color:var(--dn);margin-top:12px;font-size:13px}
svg{vertical-align:middle}
.seg{display:flex;gap:6px;justify-content:flex-end;margin-bottom:6px}.seg button.on{background:var(--ac);color:#fff;border-color:var(--ac)}
body.f-hi .r[data-hi="0"]{display:none}body.f-today .sx{display:none}body.f-today .r[data-td="0"]{display:none}
.ev.x{background:transparent;border:1px dashed var(--bd);color:var(--mu)}.nt-in{width:100%;min-width:160px;padding:5px 7px;border:1px solid var(--bd);border-radius:7px;background:var(--bg);color:var(--tx);font:inherit}
.pair,.cals{display:grid;grid-template-columns:minmax(0,1.65fr) minmax(0,1fr);gap:12px;align-items:start}
.cals{grid-template-columns:1fr 1fr}
.pair>.sec,.cals>.sec{margin-top:0}
@media(max-width:1450px){.pair,.cals{grid-template-columns:minmax(0,1fr)}.pair>.sec+.sec,.cals>.sec+.sec{margin-top:14px}}
.pair .r.daily,.pair .rh.daily{grid-template-columns:minmax(110px,1.3fr) minmax(90px,1fr) auto 84px}
@media(max-width:1450px){.pair .r.daily,.pair .rh.daily{grid-template-columns:minmax(150px,1.6fr) minmax(120px,1fr) minmax(160px,1.3fr) 124px}}
.tr svg{max-width:100%}
body.f-today .pair{grid-template-columns:minmax(0,1fr)}
.rc3{grid-template-columns:minmax(130px,1.3fr) auto minmax(0,1fr);align-items:center}.rc3 .sg small{display:none}
.ses{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}.sc{display:flex;flex-direction:column;gap:3px;align-items:flex-start}
.fg{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px}.fg label,.card label{display:block;font-size:12px;color:var(--mu)}
.fc2{width:100%;padding:6px 8px;margin-top:3px;border:1px solid var(--bd);border-radius:7px;background:var(--bg);color:var(--tx);font:inherit}
#nw{position:fixed;top:0;left:0;right:0;z-index:50;background:var(--dn);color:#fff;padding:10px 16px;text-align:center;font-weight:650}
#nw[hidden]{display:none}#nw .nl{font-weight:400;font-size:12px;margin-top:3px;opacity:.95}
.tvbox{height:430px;padding:0;overflow:hidden}.tvh{height:520px}.seg{flex-wrap:wrap}
"""

JS = """
const $=id=>document.getElementById(id);
const SS=[{n:'Tokio',tz:'Asia/Tokyo',o:[9,0],c:[15,0],d:[1,2,3,4,5],t:['otwarta','zamknięta'],l:['do zamknięcia','do otwarcia']},
{n:'Londyn',tz:'Europe/London',o:[8,0],c:[16,30],d:[1,2,3,4,5],t:['otwarta','zamknięta'],l:['do zamknięcia','do otwarcia']},
{n:'USA (cash)',tz:'America/New_York',o:[9,30],c:[16,0],d:[1,2,3,4,5],t:['otwarta','zamknięta'],l:['do zamknięcia','do otwarcia']},
{n:'Przerwa CME',tz:'America/New_York',o:[17,0],c:[18,0],d:[1,2,3,4],t:['trwa','nie trwa'],l:['do końca','do początku']}];
const WD={Mon:1,Tue:2,Wed:3,Thu:4,Fri:5,Sat:6,Sun:7};
function lt(tz){const p=Object.fromEntries(new Intl.DateTimeFormat('en-US',{timeZone:tz,weekday:'short',hour:'numeric',minute:'numeric',second:'numeric',hourCycle:'h23'}).formatToParts(new Date()).map(x=>[x.type,x.value]));return{d:WD[p.weekday],s:+p.hour*3600+ +p.minute*60+ +p.second}}
function fm(s){const h=Math.floor(s/3600),m=Math.floor(s%3600/60);return (h?h+' h ':'')+m+' min '+(s%60)+' s'}
function ses(){const e=$('ses');if(!e)return;e.innerHTML=SS.map(x=>{const l=lt(x.tz),o=x.o[0]*3600+x.o[1]*60,c=x.c[0]*3600+x.c[1]*60;const op=x.d.includes(l.d)&&l.s>=o&&l.s<c;let left=0;if(op)left=c-l.s;else{for(let k=0;k<8;k++){const dd=(l.d-1+k)%7+1;if(x.d.includes(dd)&&(k>0||l.s<o)){left=k*86400+o-l.s;break}}}return '<div class="sc"><b>'+x.n+'</b><span class="pill '+(op?'up':'')+'">'+x.t[op?0:1]+'</span><small>'+x.l[op?0:1]+': '+fm(left)+'</small></div>'}).join('')}
let ctx=null,sndOn=false;const fired=new Set();try{sndOn=localStorage.getItem('snd')==='1'}catch(x){}
function enSnd(){if(!ctx){const A=window.AudioContext||window.webkitAudioContext;if(A)ctx=new A()}if(ctx&&ctx.state==='suspended')ctx.resume()}
function beep(){if(!ctx)return;enSnd();[0,.35,.7].forEach(d=>{const o=ctx.createOscillator(),g=ctx.createGain(),t=ctx.currentTime+d;o.frequency.value=880;o.connect(g);g.connect(ctx.destination);g.gain.setValueAtTime(.0001,t);g.gain.exponentialRampToValueAtTime(.4,t+.02);g.gain.exponentialRampToValueAtTime(.0001,t+.25);o.start(t);o.stop(t+.3)})}
document.addEventListener('click',()=>{if(sndOn)enSnd()});
function ne(){try{return JSON.parse($('d1').dataset.ne)}catch(x){return[]}}
function tick(){const d1=$('d1');if(!d1)return;const now=Date.now()/1000;
const e=$('nx');if(e){const s=Math.max(0,Math.floor(+e.dataset.ts-now)),d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60);e.textContent=(d?d+' d ':'')+h+' h '+m+' min '+(s%60)+' s'}
const NE=ne(),NB=+d1.dataset.nb||5,LD=+d1.dataset.lead||60,el=$('nw'),a=NE.find(x=>now>=x.t-NB*60&&now<=x.t+x.a*60);
if(!a)el.hidden=true;else{el.hidden=false;const dt=a.t-now;el.querySelector('.nt1').textContent=dt>0?('OKNO NEWSOWE: '+a.n+' za '+fm(Math.ceil(dt))):('PO PUBLIKACJI: '+a.n+' ('+fm(Math.floor(-dt))+' temu), poczekaj na pierwszą świecę 5-min')}
if(sndOn&&ctx)NE.forEach(x=>{const dt=x.t-now;if(dt<=LD&&dt>LD-15&&!fired.has(x.t)){fired.add(x.t);beep()}});
ses()}
function eff(){const t=document.documentElement.dataset.theme;return t||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light')}
function thl(){const b=$('thb'),t=document.documentElement.dataset.theme;if(b)b.textContent='Motyw: '+(t==='dark'?'ciemny':t==='light'?'jasny':'auto')}
function thc(){const c=document.documentElement.dataset.theme||'',n=c===''?'dark':c==='dark'?'light':'';if(n)document.documentElement.dataset.theme=n;else delete document.documentElement.dataset.theme;try{n?localStorage.setItem('th',n):localStorage.removeItem('th')}catch(x){}thl();tv()}
function tv(){const r=$('tv');if(!r)return;const th=eff(),mk=(id,src,cfg)=>{const c=$(id);if(!c)return;c.innerHTML='';const w=document.createElement('div');w.className='tradingview-widget-container';w.style.height='100%';const i=document.createElement('div');i.className='tradingview-widget-container__widget';i.style.height='100%';w.appendChild(i);const s=document.createElement('script');s.src='https://s3.tradingview.com/external-embedding/'+src;s.async=true;s.text=JSON.stringify(cfg);w.appendChild(s);c.appendChild(w)};
const base={autosize:true,theme:th,locale:'pl',timezone:'Europe/Warsaw',style:'1',allow_symbol_change:false,hide_side_toolbar:true,calendar:false,interval:r.dataset.int};
mk('tv-nq','embed-widget-advanced-chart.js',Object.assign({},base,{symbol:r.dataset.nq}));
mk('tv-au','embed-widget-advanced-chart.js',Object.assign({},base,{symbol:r.dataset.gold}));
mk('tv-ev','embed-widget-events.js',{width:'100%',height:'100%',colorTheme:th,isTransparent:false,locale:'pl',importanceFilter:'0,1',countryFilter:'us'});
mk('tv-hm','embed-widget-stock-heatmap.js',{exchanges:[],dataSource:'NASDAQ100',grouping:'sector',blockSize:'market_cap_basic',blockColor:'change',locale:'pl',colorTheme:th,hasTopBar:false,isDataSetEnabled:false,isZoomEnabled:true,hasSymbolTooltip:true,isMonoSize:false,width:'100%',height:'100%'})}
let cur='';try{cur=localStorage.getItem('mm')||''}catch(x){}
function sm(m){cur=m;document.body.classList.remove('f-hi','f-today');if(m)document.body.classList.add(m);document.querySelectorAll('.seg button[data-m]').forEach(b=>b.classList.toggle('on',b.dataset.m===m));try{localStorage.setItem('mm',m)}catch(x){}}
async function post(u,o,btn,msg){try{const r=await fetch(u,{method:'POST',body:JSON.stringify(o)});if(!r.ok)throw 0;location.reload()}catch(x){btn.textContent=msg}}
function bind(){document.querySelectorAll('.seg button[data-m]').forEach(b=>b.onclick=()=>sm(b.dataset.m));
const sv=$('sv');if(sv)sv.onclick=async()=>{const o={};document.querySelectorAll('.fc').forEach(i=>o[i.dataset.sid]=i.value);try{const r=await fetch('/save',{method:'POST',body:JSON.stringify(o)});if(!r.ok)throw 0;location.reload()}catch(x){$('svm').textContent='Nie zapisano. Otwórz stronę przez http://localhost:__PORT__ (nie z pliku).'}};
const sn=$('svn');if(sn)sn.onclick=()=>{const o={};document.querySelectorAll('.nt-in').forEach(i=>o[i.dataset.k]=i.value);post('/note',o,sn,'Nie zapisano (otwórz przez localhost)')};
const tb=$('thb');if(tb)tb.onclick=thc;
const sb=$('snb');if(sb){sb.textContent='Dźwięk: '+(sndOn?'wł.':'wył.');sb.onclick=()=>{sndOn=!sndOn;try{localStorage.setItem('snd',sndOn?'1':'0')}catch(x){}sb.textContent='Dźwięk: '+(sndOn?'wł.':'wył.');if(sndOn){enSnd();beep()}}}
sm(cur);thl()}
async function refresh(){const a=document.activeElement;if(a&&a.tagName==='INPUT')return;try{const r=await fetch(location.href,{cache:'no-store'});if(!r.ok)throw 0;const d=new DOMParser().parseFromString(await r.text(),'text/html');['d1','d2'].forEach(id=>{const n=d.getElementById(id),o=$(id);if(n&&o){o.innerHTML=n.innerHTML;if(id==='d1'){['ne','lead','nb'].forEach(k=>o.dataset[k]=n.dataset[k])}}});bind()}catch(x){if(location.protocol==='file:')location.reload()}}
bind();tv();tick();setInterval(tick,1000);setInterval(refresh,300000);
"""


def specials(y):
    out = {}
    add = lambda d, t: out.setdefault(d, []).append(t)
    for k, v in CLOSED.items():
        add(date.fromisoformat(k), "Giełda zamknięta: " + v)
    for k, v in EARLY.items():
        add(date.fromisoformat(k), v)
    for m in range(1, 13):
        d = date(y, m, 15)
        d += timedelta(days=(4 - d.weekday()) % 7)
        if d.isoformat() in CLOSED:
            d -= timedelta(days=1)
        if m % 3 == 0:
            add(d, "Wygaśnięcie NQ + OpEx kwartalny")
            add(d - timedelta(days=8), "Rollover NQ (zwykle)")
        else:
            add(d, "OpEx miesięczny")
    return out


def sp(d):
    SP.setdefault(d.year, specials(d.year))
    return SP[d.year].get(d, [])


def dpl(d):
    return f"{DNI[d.weekday()]} {d.strftime('%d.%m')}"


def shorten(names, n=3):
    return ", ".join(names[:n]) + (f" +{len(names) - n}" if len(names) > n else "")


def val(x, unit):
    num = f"{x:,.2f}".replace(",", " ")
    return f"<span class='v'>{num}</span> <span class='u'>{html.escape(unit)}</span>"


def build_calendar(rows):
    groups = {}
    for r in rows:
        for dt in r.get("all", []):
            groups.setdefault((dt.date(), dt.strftime("%H:%M"), "n"), []).append(r["name"])
    for s in FOMC:
        groups.setdefault((date.fromisoformat(s), "20:00", "f"), []).append("FOMC (decyzja)")
    for dt, n in LAST.get("earn", []):
        groups.setdefault((dt.date(), dt.strftime("%H:%M"), "e"), []).append(n)
    for dt, n in custom_events():
        groups.setdefault((dt.date(), dt.strftime("%H:%M"), "n"), []).append(n)
    ev = {}
    for (d, t, k), names in sorted(groups.items(), key=lambda x: (x[0][0], x[0][1])):
        kk = "h" if k == "n" and any(x in HIGH for x in names) else k
        ev.setdefault(d, []).append((t, shorten(names, 2), ", ".join(names), kk))
    today = datetime.now(WAW).date()
    out = []
    for k in range(2):
        mm = today.month + k
        yy, mm = today.year + (mm - 1) // 12, (mm - 1) % 12 + 1
        cells = "".join(f"<div class='dh'>{d}</div>" for d in ("Pn", "Wt", "Śr", "Cz", "Pt", "So", "Nd"))
        for d in calendar.Calendar(0).itermonthdates(yy, mm):
            if d.month != mm:
                cells += "<div class='d o'></div>"
                continue
            items = "".join(f"<div class='ev {kk}' title='{html.escape(full)}'><b>{t}</b> {html.escape(n)}</div>"
                            for t, n, full, kk in ev.get(d, []))
            items += "".join(f"<div class='ev x'>{html.escape(t)}</div>" for t in sp(d))
            cls = "d" + (" t" if d == today else "") + (" wk" if d.weekday() >= 5 else "")
            cells += f"<div class='{cls}'><span class='num'>{d.day}</span>{items}</div>"
        out.append(f"<div class='card sec sx'><h2>Kalendarz: {MONTHS[mm]} {yy} (czas PL)</h2>"
                   f"<div class='w'><div class='cal'>{cells}</div></div></div>")
    return "".join(out)


def spark(vals, w=116, h=30):
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    pts = [f"{i * (w - 6) / (len(vals) - 1) + 3:.1f},{h - 4 - (v - lo) / rng * (h - 8):.1f}" for i, v in enumerate(vals)]
    lx, ly = pts[-1].split(",")
    return (f"<svg width='{w}' height='{h}' viewBox='0 0 {w} {h}'><title>min {lo:.2f} / max {hi:.2f}</title>"
            f"<polyline points='{' '.join(pts)}' fill='none' stroke='#8a93a3' stroke-width='1.5' stroke-linejoin='round'/>"
            f"<circle cx='{lx}' cy='{ly}' r='2.8' fill='#2563eb'/></svg>")


def pills(r):
    if r["sig"] == "none":
        return "<span class='pill'>informacyjnie</span>"
    if r["sig"] == "flat":
        return "<span class='pill'>bez zmiany</span>"
    a, _, cls = arrows(r["sig"])
    return (f"<span class='pill {cls}'>NQ {a}</span><span class='pill {cls}'>Au {a}</span>"
            f"<small>vs {r['basis']}</small>")


def build_recent(rows):
    now = datetime.now(WAW)
    items = sorted((r for r in rows if r.get("last") and now - r["last"] <= timedelta(days=14)),
                   key=lambda r: r["last"], reverse=True)[:8]
    if not items:
        return "<div class='card'><h2>Ostatnie wydarzenia (14 dni)</h2><small>Brak publikacji w tym okresie.</small></div>"
    lines = "".join(
        f"<div class='it rc3'><div><b>{html.escape(r['name'])}</b><br><small>{dpl(r['last'])} {r['last'].strftime('%H:%M')} · było {fmt(r['prev'], r['unit'])}</small></div>"
        f"<div>{val(r['now'], r['unit'])}</div><div class='sg'>{pills(r)}</div></div>" for r in items)
    return (f"<div class='card'><h2>Ostatnie wydarzenia (14 dni)</h2>{lines}"
            "<small>Dane z FRED mogą pojawiać się z opóźnieniem po publikacji.</small></div>")


def custom_events():
    out = []
    for e in CFG.get("custom_events", []):
        try:
            out.append((datetime.combine(date.fromisoformat(e["date"]), dtime.fromisoformat(e.get("time", "00:00")), WAW), e["name"]))
        except Exception:
            pass
    return out


def build_tv():
    if not CFG.get("tv_widgets", True):
        return ""
    tv, q = CFG.get("tv", {}), lambda k, d: html.escape(str(tv.get(k, d)), quote=True)
    return (f'<div id="tv" data-nq="{q("nq", "OANDA:NAS100USD")}" data-gold="{q("gold", "OANDA:XAUUSD")}" data-int="{q("interval", "5")}">'
            '<div class="cols sec"><div class="card tvbox" id="tv-nq"></div><div class="card tvbox" id="tv-au"></div></div>'
            '<div class="cols sec"><div class="card tvbox tvh" id="tv-ev"></div><div class="card tvbox tvh" id="tv-hm"></div></div>'
            '<small>Wykresy, kalendarz z konsensusem i mapa sektorów to widżety TradingView (dane mogą być opóźnione, a instrumenty CFD '
            'mogą różnić się od kontraktów futures).</small></div>')


def fnum(v):
    return "–" if v is None else f"{v:,.1f}".replace(",", " ")


def build_levels(rows):
    if not LEV:
        return "<div class='card'><h2>Poziomy dnia</h2><small>Wymaga yfinance (pip install yfinance) i dostępu do danych.</small></div>"
    iv = {r["sid"]: r["now"] for r in rows}
    spec = [("Cena (opóźniona)", None), ("Oczekiwany ruch dnia (VXN / GVZ)", "exp"), ("ATR(14)", "atr"),
            ("Poprzedni high", "yh"), ("Poprzedni low", "yl"), ("Poprzedni close", "yc"), ("Pivot", "p"),
            ("R1", "r1"), ("R2", "r2"), ("S1", "s1"), ("S2", "s2"), ("Nocny high", "oh"), ("Nocny low", "ol"),
            ("Tydzień high", "wh"), ("Tydzień low", "wl")]
    body = ""
    for lab, k in spec:
        cells = []
        for sym, sid in (("NQ=F", "VXNCLS"), ("GC=F", "GVZCLS")):
            pr, v = PXV.get(sym, (0, None))[1], iv.get(sid)
            if k is None:
                cells.append(fnum(pr))
            elif k == "exp":
                cells.append(f"±{fnum(pr * v / 100 / 252 ** 0.5)} pkt (±{v / 252 ** 0.5:.2f}%)" if pr and v else "–")
            else:
                cells.append(fnum(LEV.get(sym, {}).get(k)))
        body += f"<tr><td>{lab}</td><td>{cells[0]}</td><td>{cells[1]}</td></tr>"
    return ("<div class='card'><h2>Poziomy dnia (kontrakty futures)</h2><div class='w'><table class='tbl'>"
            f"<tr><th></th><th>NQ</th><th>Złoto</th></tr>{body}</table></div>"
            "<small>Dane z yfinance (opóźnione). Nocny zakres: od 22:00 PL do 15:30 PL. Oczekiwany ruch = cena × IV / √252.</small></div>")


def build_agenda(rows):
    ev = upcoming(rows)[:8]
    if not ev:
        return "<div class='card'><h2>Najbliższe wydarzenia</h2><small>Brak danych o kolejnych publikacjach.</small></div>"
    lines = "".join(
        f"<div class='it ag'><span class='dot{' hi' if any(x in HIGH for x in n) else ''}'></span>"
        f"<div><b>{html.escape(shorten(n, 4))}</b> <small>{dpl(dt)}</small></div><div class='tm'>{dt.strftime('%H:%M')}</div></div>"
        for dt, n in ev)
    return (f"<div class='card'><h2>Najbliższe wydarzenia (czas PL)</h2>{lines}"
            "<small><span class='dot hi' style='display:inline-block'></span> duży wpływ &nbsp; "
            "<span class='dot' style='display:inline-block'></span> średni wpływ</small></div>")


def render(rows, px, errors):
    now = datetime.now(WAW).strftime("%H:%M:%S")
    far = datetime.max.replace(tzinfo=WAW)
    pub = sorted((r for r in rows if not r["daily"]), key=lambda r: (r["next"] is None, r["next"] or far))
    mkt = [r for r in rows if r["daily"]]

    def row(r):
        base = (f"<div class='n'><b>{html.escape(r['name'])}</b><small>dane z {r['date']}</small></div>"
                f"<div class='vv'>{val(r['now'], r['unit'])}<small>poprz. {fmt(r['prev'], r['unit'])}</small>{extra(r)}</div>"
                f"<div class='sg'>{pills(r)}</div><div class='tr'>{spark(r['hist'])}</div>")
        if r["daily"]:
            return f"<div class='r daily' data-hi='1' data-td='0'>{base}</div>"
        nxt = f"{dpl(r['next'])} {r['next'].strftime('%H:%M')}" if r["next"] else "–"
        td = int(bool(r["next"] and r["next"].date() == datetime.now(WAW).date()))
        return f"<div class='r' data-hi='{int(r['name'] in HIGH)}' data-td='{td}'>{base}<div class='fcw'>{fcin(r)}</div><div class='nt'>{nxt}</div></div>"

    cards = "".join(f"<div class='card kpi'><div class='lb'>{html.escape(l)}</div><div class='big'>{p:,.1f}</div>"
                    f"<span class='pill {'up' if c >= 0 else 'dn'}'>{c:+.2f}%</span></div>" for l, p, c in px)
    if not px:
        cards += ("<div class='card kpi'><div class='lb'>Ceny NQ i złota</div>"
                  "<small>Zainstaluj yfinance (pip install yfinance), aby je zobaczyć.</small></div>")
    nx = upcoming(rows)
    if nx:
        dt, names = nx[0]
        risk = any(x in HIGH for d2, n in nx if d2.date() == datetime.now(WAW).date() for x in n)
        badge = " <span class='pill dn'>dzień wysokiego ryzyka</span>" if risk else ""
        cards += (f"<div class='card kpi wide'><div class='lb'>Najbliższa publikacja{badge}</div>"
                  f"<div class='big' id='nx' data-ts='{int(dt.timestamp())}'></div>"
                  f"<div><b>{html.escape(shorten(names, 4))}</b> <small>{dpl(dt)} {dt.strftime('%H:%M')} (czas PL)</small></div>{reaction_note(names)}</div>")
    err = "".join(f"<div class='err'>{html.escape(e)}</div>" for e in errors)
    nw = CFG.get("news_window", {})
    nev = [{"t": int(dt.timestamp()), "n": shorten(n, 4), "a": 30 if "FOMC (decyzja)" in n else nw.get("after", 3)}
           for dt, n in upcoming(rows, back=40) if any(x in HIGH for x in n)]
    chk = " · ".join(html.escape(x) for x in CFG.get("news_checklist", []))
    js = JS.replace("__PORT__", str(CFG.get("port", 8765)))
    nev_attr = html.escape(json.dumps(nev), quote=True)
    lead, nb = CFG.get("sound_lead_sec", 60), nw.get("before", 5)
    page = f"""<!DOCTYPE html><html lang="pl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Makro USA → NQ100 i złoto</title><script>try{{const t=localStorage.getItem('th');if(t)document.documentElement.dataset.theme=t}}catch(e){{}}</script><style>{CSS}</style></head><body><div id="nw" hidden><div class="nt1"></div><div class="nl">{chk}</div></div><main>
<div class="dyn" id="d1" data-ne='{nev_attr}' data-lead='{lead}' data-nb='{nb}'>
<header><div><h1>Makro USA → NQ100 i złoto</h1><small>Dane: FRED · godziny w czasie polskim</small></div><div style="text-align:right"><div class="seg"><button data-m="">Wszystko</button><button data-m="f-hi">Tylko ważne</button><button data-m="f-today">Dziś</button><button id="snb">Dźwięk: wył.</button><button id="thb">Motyw: auto</button></div><small>Aktualizacja: {now}</small></div></header>
<div class="kpis">{cards}</div>{err}
<div class="card sec"><h2>Sesje i przerwa CME (odliczanie)</h2><div class="ses" id="ses"></div></div>
<div class="cols sec">{build_agenda(rows)}{build_recent(rows)}</div>
<div class="sec">{build_levels(rows)}</div>
</div>
{build_tv()}
<div class="dyn" id="d2">
<div class="pair sec"><div class="card sec tb"><h2>Publikacje makro</h2>
<div class="rh"><span>Wskaźnik</span><span>Odczyt</span><span>Sygnał</span><span>Trend</span><span>Twoja prognoza</span><span>Następna</span></div>
{''.join(row(r) for r in pub)}
<div class="foot"><button id="sv">Zapisz prognozy</button> <small id="svm">Wpisz prognozę z kalendarza ekonomicznego (jednostki jak w „Odczyt”). Po publikacji pole czyści się samo.</small></div></div>
<div class="card sec tb sx"><h2>Rynek na bieżąco (dane dzienne, opóźnienie ok. 1 dnia)</h2>
<div class="rh daily"><span>Wskaźnik</span><span>Wartość</span><span>Sygnał</span><span>Trend</span></div>
{''.join(row(r) for r in mkt)}</div></div>
{build_stats()}<div class="cals sec">{build_calendar(rows)}</div>
</div>
<p class="sec"><small>NQ ▲/▼ i Au ▲/▼ to typowa reakcja na odczyt względem prognozy lub poprzedniego odczytu. To uproszczony schemat, nie rekomendacja.</small></p>
</main><script>{js}</script></body></html>"""
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(page)


def save(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def get_state():
    st = load(STATE_PATH, {})
    for k, v in (("seen", {}), ("alerted", []), ("sched", {}), ("pending", []), ("fp", {}), ("rev", {})):
        st.setdefault(k, v)
    return st


def forecasts(cfg):
    f = dict(cfg.get("forecasts", {}))
    f.update(load(FC_PATH, {}))
    return f


def clear_forecast(sid):
    f = load(FC_PATH, {})
    if f.pop(sid, None) is not None:
        save(FC_PATH, f)


def extra(r):
    b = []
    if r.get("yoy") is not None:
        b.append(f"r/r {r['yoy']:.2f}% (poprz. {r['yoyp']:.2f}%)")
    if r.get("rev") is not None and abs(r["rev"]) >= 0.005:
        b.append(f"rewizja poprz.: {r['rev']:+.2f} {html.escape(r['unit'])}")
    return "".join(f"<small>{x}</small>" for x in b)


def fcin(r):
    if r["daily"]:
        return ""
    v = "" if r["fc"] is None else r["fc"]
    return f"<input class='fc' data-sid='{r['sid']}' value='{v}' placeholder='{html.escape(r['unit'])}' inputmode='decimal'>"


def reapply(cfg):
    fcs = forecasts(cfg)
    for r in LAST["rows"]:
        r["fc"] = fcs.get(r["sid"])
        r["sig"], r["basis"], _ = signal(r["ind"], r["now"], r["prev"], r["fc"])
    render(LAST["rows"], LAST["px"], LAST["errors"])


# ---------- dziennik reakcji rynku ----------
def intraday():
    import yfinance as yf
    return {sym: yf.Ticker(sym).history(period="5d", interval="1m")["Close"].dropna() for sym in PRICES}


def price_at(series, t):
    s2 = series[series.index <= t - timedelta(minutes=1)]  # świeca z minuty t-1 zamyka się o czasie t
    if len(s2) == 0 or (t - s2.index[-1]).total_seconds() > 360:
        return None
    return float(s2.iloc[-1])


JCOLS = ["data", "wskaznik", "odczyt", "poprzedni", "prognoza", "sygnal", "odchylenie",
         "NQ_cena", "NQ_5m", "NQ_15m", "NQ_60m", "ZLOTO_cena", "ZLOTO_5m", "ZLOTO_15m", "ZLOTO_60m"]


def write_journal(e):
    row = {"data": e["rdt"][:16].replace("T", " "), "wskaznik": e["name"], "odczyt": round(e["now"], 3),
           "poprzedni": round(e["prev"], 3), "prognoza": "" if e["fc"] is None else e["fc"],
           "sygnal": e["sig"], "odchylenie": round(e["d"], 3)}
    for sym, lab in (("NQ=F", "NQ"), ("GC=F", "ZLOTO")):
        p0 = (e["p"].get("0") or {}).get(sym)
        row[lab + "_cena"] = round(p0, 2) if p0 else ""
        for o in OFFS[1:]:
            p = (e["p"].get(str(o)) or {}).get(sym)
            row[f"{lab}_{o}m"] = round((p / p0 - 1) * 100, 3) if p0 and p else ""
    new = not os.path.exists(JOURNAL_PATH)
    with open(JOURNAL_PATH, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, JCOLS)
        if new:
            w.writeheader()
        w.writerow(row)


def update_journal(state):
    now = datetime.now(WAW)
    pend = state["pending"]
    due = lambda e, o: str(o) not in e["p"] and now >= datetime.fromisoformat(e["rdt"]) + timedelta(minutes=o + 1)
    need = [e for e in pend if any(due(e, o) for o in OFFS)]
    if need:
        try:
            data = intraday()
            for e in need:
                r = datetime.fromisoformat(e["rdt"])
                for o in OFFS:
                    if due(e, o):
                        t = r + timedelta(minutes=o)
                        e["p"][str(o)] = {sym: price_at(data[sym], t) for sym in PRICES}
        except Exception as ex:
            print("Dziennik (brak danych intraday, czy zainstalowano yfinance?):", ex)
    done = [e for e in pend if len(e["p"]) == len(OFFS) or now - datetime.fromisoformat(e["rdt"]) > timedelta(days=2)]
    for e in done:
        if e["p"]:
            write_journal(e)
    state["pending"] = [e for e in pend if e not in done]


def read_journal():
    try:
        with open(JOURNAL_PATH, encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def avgk(rs, k):
    v = [float(x[k]) for x in rs if x.get(k)]
    return f"{sum(v) / len(v):+.2f}%" if v else "–"


def hit(rs, k, sg):
    v = [float(x[k]) for x in rs if x.get(k) and float(x[k]) != 0]
    if sg not in ("hawk", "dove") or not v:
        return "–"
    ok = sum(1 for x in v if (x < 0) == (sg == "hawk"))
    return f"{ok / len(v) * 100:.0f}% ({ok}/{len(v)})"


def reaction_note(names):
    jr = read_journal()
    for n in names:
        rs = [r for r in jr if r["wskaznik"] == n]
        parts = []
        for sg, lab in (("hawk", "jastrzębim"), ("dove", "gołębim")):
            x = [r for r in rs if r["sygnal"] == sg]
            if x:
                parts.append(f"po {lab}: NQ {avgk(x, 'NQ_15m')}, złoto {avgk(x, 'ZLOTO_15m')} (n={len(x)})")
        if parts:
            return f"<div><small>{html.escape(n)} w Twoim dzienniku (15 min): {'; '.join(parts)}</small></div>"
    return ""


def evening(cfg, state):
    now = datetime.now(WAW)
    today = now.date().isoformat()
    h, m = (int(x) for x in cfg["evening_time"].split(":"))
    if (not cfg.get("evening_summary", True) or state.get("evening") == today or now.time() < dtime(h, m)
            or now.weekday() > 4 or not LAST["rows"]):
        return
    state["evening"] = today
    done = [r for r in LAST["rows"] if r.get("last") and r["last"].date() == now.date()]
    if not done:
        return
    lab = {"hawk": "jastrzębio", "dove": "gołębio", "flat": "bez zmiany"}
    lines = [f"🌙 Podsumowanie dnia {dpl(now)}"]
    lines += [f"• {r['name']}: {fmt(r['now'], r['unit'])} (poprz. {fmt(r['prev'], r['unit'])}), {lab.get(r['sig'], '')}" for r in done]
    for x in (x for x in read_journal() if x["data"].startswith(today)):
        lines.append(f"  reakcja {x['wskaznik']} (15/60 min): NQ {x['NQ_15m'] or '–'}% / {x['NQ_60m'] or '–'}%, "
                     f"złoto {x['ZLOTO_15m'] or '–'}% / {x['ZLOTO_60m'] or '–'}%")
    lines += [f"{l}: {p:,.1f} ({c:+.2f}%)" for l, p, c in LAST["px"]]
    notify(cfg, "\n".join(lines), "Makro USA – podsumowanie dnia")


def build_stats():
    rows = read_journal()
    if not rows:
        return ("<div class='card sec sx'><h2>Dziennik reakcji rynku</h2><small>Na razie pusty. Wpisy pojawią się po pierwszych "
                "publikacjach, gdy program będzie włączony (wymaga yfinance). Plik: journal.csv</small></div>")
    g = {}
    for r in rows:
        g.setdefault((r["wskaznik"], r["sygnal"]), []).append(r)

    def avg(rs, k):
        v = [float(x[k]) for x in rs if x.get(k)]
        return f"{sum(v) / len(v):+.2f}%" if v else "–"
    lab = {"hawk": "jastrzębi ▼", "dove": "gołębi ▲", "flat": "neutralny"}
    body = "".join(f"<tr><td>{html.escape(n)}</td><td>{lab.get(sg, sg)}</td><td>{len(rs)}</td><td>{avg(rs, 'NQ_15m')}</td>"
                   f"<td>{avg(rs, 'NQ_60m')}</td><td>{avg(rs, 'ZLOTO_15m')}</td><td>{avg(rs, 'ZLOTO_60m')}</td><td>{hit(rs, 'NQ_15m', sg)}</td><td>{hit(rs, 'ZLOTO_15m', sg)}</td></tr>"
                   for (n, sg), rs in sorted(g.items()))
    notes = load(NOTES_PATH, {})
    last = "".join(
        f"<tr><td>{r['data']}</td><td>{html.escape(r['wskaznik'])}</td><td>{lab.get(r['sygnal'], '')}</td>"
        f"<td>{r['NQ_15m'] or '–'}%</td><td>{r['ZLOTO_15m'] or '–'}%</td>"
        f"<td><input class='nt-in' data-k='{html.escape(r['data'] + '|' + r['wskaznik'], quote=True)}' "
        f"value='{html.escape(notes.get(r['data'] + '|' + r['wskaznik'], ''), quote=True)}' placeholder='notatka'></td></tr>"
        for r in rows[-10:][::-1])
    return (f"<div class='card sec sx'><h2>Dziennik reakcji rynku ({len(rows)} publikacji)</h2><div class='w'><table class='tbl'><tr><th>Wskaźnik</th><th>Odczyt</th>"
            f"<th>Ile razy</th><th>NQ po 15 min</th><th>NQ po 60 min</th><th>Złoto po 15 min</th><th>Złoto po 60 min</th><th>Trafność NQ 15 min</th><th>Trafność Au 15 min</th></tr>{body}</table></div>"
            "<small>Trafność = jak często kierunek ▲/▼ zgodził się z ruchem ceny. Mała próba = słaba wiarygodność.</small>"
            f"<h2 style='margin-top:16px'>Ostatnie wpisy i notatki</h2><div class='w'><table class='tbl'><tr><th>Data</th><th>Wskaźnik</th><th>Odczyt</th>"
            f"<th>NQ 15 min</th><th>Złoto 15 min</th><th>Notatka</th></tr>{last}</table></div>"
            "<p><button id='svn'>Zapisz notatki</button></p></div>")


# ---------- plan dnia ----------
def morning(cfg, ev, state):
    now = datetime.now(WAW)
    today = now.date().isoformat()
    h, m = (int(x) for x in cfg["morning_time"].split(":"))
    if not cfg.get("morning_briefing", True) or state.get("briefing") == today or now.time() < dtime(h, m):
        return
    state["briefing"] = today
    items = [(dt, n) for dt, n in ev if dt.date() == now.date()]
    spt = sp(now.date())
    if not items and not spt:
        return
    hi = lambda n: any(x in HIGH for x in n)
    lines = [f"📅 Dziś {dpl(now)}: " + ("DZIEŃ WYSOKIEGO RYZYKA 🔴" if any(hi(n) for _, n in items) else "umiarkowany dzień 🟡" if items else "brak publikacji")]
    lines += [f"{dt.strftime('%H:%M')}  {', '.join(n)} {'🔴' if hi(n) else '🟡'}" for dt, n in items]
    lines += [f"⚠️ {t}" for t in spt]
    lines.append("Uważaj na wejścia tuż przed publikacjami.")
    notify(cfg, "\n".join(lines), "Makro USA – plan dnia")


# ---------- serwer lokalny (formularz prognoz) ----------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _local(self):
        return self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost")

    def do_GET(self):
        if not self._local():
            return self.send_error(403)
        try:
            with open(OUT_PATH, "rb") as f:
                data = f.read()
        except Exception:
            data = "Brak danych, poczekaj na pierwsze odświeżenie.".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        try:
            if not self._local() or self.path not in ("/save", "/note"):
                return self.send_error(403)
            d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            if self.path == "/note":
                nt = load(NOTES_PATH, {})
                for k, v in d.items():
                    v = str(v).strip()[:300]
                    if v:
                        nt[str(k)[:80]] = v
                    else:
                        nt.pop(k, None)
                save(NOTES_PATH, dict(list(nt.items())[-500:]))
                reapply(CFG)
                self.send_response(200)
                self.end_headers()
                return
            ok = {i[1] for i in IND if not i[8]}
            fc = load(FC_PATH, {})
            for k, v in d.items():
                v = str(v).strip().replace(",", ".")
                if k in ok:
                    if v == "":
                        fc.pop(k, None)
                    else:
                        fc[k] = float(v)
            save(FC_PATH, fc)
            reapply(CFG)
            self.send_response(200)
            self.end_headers()
        except Exception:
            self.send_error(400)


def start_server(cfg):
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", int(cfg["port"])), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        print(f"Dashboard z formularzem prognoz: http://localhost:{cfg['port']}")
    except OSError as e:
        print("Nie udało się uruchomić serwera (port zajęty?):", e)


def cycle(cfg):
    state = get_state()
    first = not state["seen"]
    fcs = forecasts(cfg)
    now = datetime.now(WAW)
    rows, errors = [], []
    for ind in IND:
        name, sid = ind[0], ind[1]
        try:
            m = metric(cfg, ind)
            fc = fcs.get(sid)
            sig, basis, d = signal(ind, m["now"], m["prev"], fc)
            lst = next_release(cfg, ind)
            nxt = lst[0] if lst else None
            rows.append(dict(ind=ind, sid=sid, last=PAST.get(sid), name=name, unit=ind[5], daily=ind[8], next=nxt, all=lst,
                             sig=sig, basis=basis, fc=fc, **m))
            new = (not ind[8]) and state["seen"].get(sid) not in (None, m["date"]) and not first
            cur = state["sched"].get(sid)
            if new:
                a, _, _ = arrows(sig)
                notify(cfg, f"📊 {name}: {fmt(m['now'], ind[5])} (poprz. {fmt(m['prev'], ind[5])})\n"
                            f"vs {basis}: {'jastrzębio' if sig == 'hawk' else 'gołębio' if sig == 'dove' else 'neutralnie'} → NQ {a}, złoto {a}")
                rdt = cur if cur and datetime.fromisoformat(cur) <= now else now.isoformat()
                state["pending"].append(dict(rdt=rdt, name=name, sid=sid, now=m["now"], prev=m["prev"],
                                             fc=fc, sig=sig, d=d, p={}))
                clear_forecast(sid)
                if sid in state["fp"]:
                    state["rev"][sid] = round(m["prev"] - state["fp"][sid], 4)
            if nxt and (new or cur is None or datetime.fromisoformat(cur) > now):
                state["sched"][sid] = nxt.isoformat()
            if not ind[8]:
                if state["seen"].get(sid) != m["date"]:
                    state["fp"][sid] = m["now"]
                rows[-1]["rev"] = state["rev"].get(sid)
            state["seen"][sid] = m["date"]
        except Exception as e:
            errors.append(f"{name}: {e}")
    px = prices()
    LEV.clear()
    LEV.update(levels())
    LAST.update(rows=rows, px=px, errors=errors, earn=earnings(cfg, state))
    ev = upcoming(rows)
    send_alerts(cfg, ev, state)
    morning(cfg, ev, state)
    evening(cfg, state)
    check_levels(cfg, rows, state)
    update_journal(state)
    render(rows, px, errors)
    save(STATE_PATH, state)
    print(datetime.now(WAW).strftime("%H:%M:%S"), f"OK: {len(rows)} wskaźników, błędów: {len(errors)}")
    for e in errors:
        print("  ", e)
    return ev


def export(cfg, years):
    """Eksport historii odczytów: historia_odczytow.csv (+ .xlsx, jeśli jest openpyxl) oraz dziennik reakcji."""
    data = {}
    for ind in IND:
        per = 260 if ind[8] else 52 if ind[1] == "ICSA" else 4 if ind[1] == "A191RL1Q225SBEA" else 12
        try:
            data[ind[0]] = (ind, series_m(cfg, ind, per * years + 2))
        except Exception as e:
            print("Pominięto", ind[0], e)
    path = os.path.join(BASE, "historia_odczytow.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["wskaznik", "series_id", "jednostka", "data", "wartosc"])
        for name, (ind, rows) in data.items():
            for d, v in reversed(rows):
                w.writerow([name, ind[1], ind[5], d, round(v, 4)])
    print("Zapisano:", path)
    try:
        from openpyxl import Workbook
    except ImportError:
        print("Brak openpyxl, zapisano tylko CSV. Dla pliku Excel: pip install openpyxl")
        return
    wb = Workbook()
    ws = wb.active
    ws.title = "Podsumowanie"
    ws.append(["Wskaźnik", "Jednostka", "Data ostatniego odczytu", "Ostatni", "Poprzedni"])
    for name, (ind, rows) in data.items():
        if len(rows) >= 2:
            ws.append([name, ind[5], date.fromisoformat(rows[0][0]), round(rows[0][1], 4), round(rows[1][1], 4)])
    for name, (ind, rows) in data.items():
        ws = wb.create_sheet(re.sub(r"[\[\]:*?/\\]", "-", name)[:31])
        ws.append(["Data (okres odniesienia)", f"Wartość ({ind[5]})"])
        for d, v in reversed(rows):
            ws.append([date.fromisoformat(d), round(v, 4)])
        ws.column_dimensions["A"].width = 24
    try:
        with open(JOURNAL_PATH, encoding="utf-8-sig") as f:
            jr = list(csv.reader(f))
        ws = wb.create_sheet("Dziennik reakcji")
        for i, row in enumerate(jr):
            ws.append(row if i == 0 else [float(x) if re.fullmatch(r"-?\d+(\.\d+)?", x) else x for x in row])
    except OSError:
        pass
    xl = os.path.join(BASE, "historia_odczytow.xlsx")
    wb.save(xl)
    print("Zapisano:", xl)


def autostart(remove=False):
    """Autostart na Windowsie przez folder Autostart (bez uprawnień administratora)."""
    if not sys.platform.startswith("win"):
        print("Ta opcja działa na Windowsie. Linux: crontab -e i dopisz: @reboot python3 " + os.path.abspath(__file__) +
              "\nMac: użyj launchd lub Automator (Elementy logowania).")
        return
    folder = os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup")
    bat = os.path.join(folder, "MakroMonitor.bat")
    if remove:
        if os.path.exists(bat):
            os.remove(bat)
        print("Usunięto autostart.")
        return
    py = sys.executable
    pyw = os.path.join(os.path.dirname(py), "pythonw.exe")
    if os.path.exists(pyw):
        py = pyw
    with open(bat, "w", encoding="utf-8") as f:
        f.write(f'@echo off\r\nchcp 65001 >nul\r\ntimeout /t 20 /nobreak >nul\r\n'
                f'start "" "{py}" "{os.path.abspath(__file__)}"\r\nexit\r\n')
    print("Dodano do autostartu:", bat, "\nProgram uruchomi się sam po zalogowaniu (logi: monitor.log). "
          "Aby wyłączyć: python makro_monitor.py --remove-autostart")


def main():
    global CFG
    if sys.stdout is None:  # pythonw (autostart): zapis do pliku logu
        sys.stdout = sys.stderr = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    if "--install-autostart" in sys.argv or "--remove-autostart" in sys.argv:
        return autostart(remove="--remove-autostart" in sys.argv)
    cfg = CFG = config()
    if not cfg["fred_api_key"]:
        sys.exit("Brak klucza FRED. Wpisz go w config.json (\"fred_api_key\") lub zmienną FRED_API_KEY.")
    if "--export" in sys.argv:
        i = sys.argv.index("--export") + 1
        return export(cfg, int(sys.argv[i]) if i < len(sys.argv) and sys.argv[i].isdigit() else 10)
    if "--once" in sys.argv:
        if os.getenv("GITHUB_ACTIONS") and not (cfg["telegram_token"] and cfg["telegram_chat_id"]):
            print("UWAGA: brak danych Telegrama, alerty nigdzie nie trafią.")
        return cycle(cfg)
    start_server(cfg)
    while True:
        ev = cycle(cfg)
        end = time.time() + max(1, cfg["interval_minutes"]) * 60
        while time.time() < end:
            time.sleep(30)
            st = get_state()
            send_alerts(cfg, ev, st)
            morning(cfg, ev, st)
            evening(cfg, st)
            update_journal(st)
            save(STATE_PATH, st)


if __name__ == "__main__":
    main()