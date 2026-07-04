"""
scraper.py — Collecteur de snapshots Getaround (respectueux)
============================================================

Rôle : à CHAQUE exécution, prendre une photo horodatée de l'offre et l'AJOUTER
à une base append-only. C'est la répétition dans le temps qui crée la donnée
(cf. features.py). Un seul run ne suffit pas : il faut le lancer en cron.

Ce qu'il capture par run :
  - listing_snapshots  : prix/jour, nb_avis, note, catégorie, zone, encore listée
  - calendar_snapshots : pour chaque annonce, quels jours futurs sont bookables

Deux stratégies combinées, du plus propre au plus robuste :
  (A) INTERCEPTION RÉSEAU : on écoute les réponses XHR/fetch de la page. Le front
      Getaround appelle des endpoints JSON internes (recherche + calendrier).
      Les capturer donne de la donnée structurée sans parser du HTML fragile.
  (B) FALLBACK DOM : si l'API n'est pas captée, on lit le rendu de la page.

⚠️ À CONFIRMER SUR LE SITE LIVE (change souvent) :
  - SEARCH_URL_TEMPLATE et les params de recherche par zone/catégorie/dates
  - les sélecteurs DOM (SEL_*)
  - le pattern d'URL des endpoints JSON (voir _looks_like_api)
  Lance une fois avec HEADLESS=False + DEBUG_DUMP=True pour inspecter, puis fige.

Garde-fous intégrés (lis le README) :
  - respect de robots.txt (check_robots)
  - throttling avec jitter + backoff exponentiel sur rate-limit
  - un seul onglet, séquentiel, pas de parallélisme agressif
  - arrêt propre si le message "Please try again in a few minutes" apparaît
"""

from __future__ import annotations
import time, random, json, sqlite3, urllib.robotparser, urllib.parse, datetime as dt
from pathlib import Path
from dataclasses import dataclass, field

# playwright s'installe via: pip install playwright && python -m playwright install chromium
from playwright.sync_api import sync_playwright, Response

# ------------------------------- CONFIG -------------------------------------
BASE = "https://fr.getaround.com"
DB_PATH = Path("getaround_snapshots.sqlite")
HEADLESS = True
DEBUG_DUMP = False          # True => écrit les réponses JSON captées dans ./debug/
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

# throttling — reste conservateur, c'est ce qui t'évite le rate-limit
MIN_DELAY, MAX_DELAY = 6.0, 12.0     # secondes entre deux requêtes de page
BACKOFF_BASE = 60.0                  # 1re pause après rate-limit (s), puis x2
MAX_BACKOFF_RETRIES = 4

# fenêtre calendrier à observer par annonce (jours dans le futur)
CALENDAR_HORIZON_DAYS = 35

# zones & catégories — mappe-les sur les vrais paramètres d'URL de la recherche
ZONES = {
    "Paris 12e":        dict(lat=48.8399, lng=2.3876),
    "Paris 20e":        dict(lat=48.8649, lng=2.3989),
    "Torcy/MLV":        dict(lat=48.8500, lng=2.6500),
    "Chessy/ValdEurope":dict(lat=48.8700, lng=2.7830),
    "Meaux":            dict(lat=48.9600, lng=2.8790),
}
CATEGORIES = ["Utilitaire", "Citadine", "Berline", "SUV", "Minibus",
              "Cabriolet", "Familiale"]


# ----------------------------- GARDE-FOUS -----------------------------------
def check_robots(path: str = "/") -> bool:
    """Retourne True si le scraping du chemin est autorisé par robots.txt."""
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(f"{BASE}/robots.txt")
    try:
        rp.read()
    except Exception:
        print("[robots] illisible — on suppose interdit, à vérifier manuellement")
        return False
    allowed = rp.can_fetch(USER_AGENT, BASE + path)
    print(f"[robots] {path} -> {'autorisé' if allowed else 'INTERDIT'}")
    return allowed


def polite_sleep():
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


class RateLimited(Exception):
    pass


def _is_rate_limited(page) -> bool:
    try:
        txt = page.content().lower()
    except Exception:
        return False
    return "try again in a few minutes" in txt or "réessayez dans" in txt


# ----------------------------- STOCKAGE -------------------------------------
def init_db(con: sqlite3.Connection):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS listing_snapshots(
        listing_id TEXT, snapshot_ts TEXT, zone TEXT, categorie TEXT,
        prix_jour_eur REAL, nb_avis INTEGER, note_moyenne REAL,
        still_listed INTEGER, url TEXT,
        PRIMARY KEY(listing_id, snapshot_ts));
    CREATE TABLE IF NOT EXISTS calendar_snapshots(
        listing_id TEXT, snapshot_ts TEXT, target_date TEXT,
        available INTEGER, price_day_eur REAL,
        PRIMARY KEY(listing_id, snapshot_ts, target_date));
    CREATE TABLE IF NOT EXISTS run_log(
        snapshot_ts TEXT, zone TEXT, categorie TEXT, n_listings INTEGER,
        note TEXT);
    """)
    con.commit()


# --------------------------- INTERCEPTION -----------------------------------
def _looks_like_api(url: str) -> str | None:
    """Classe une réponse réseau. Adapte les motifs après inspection live."""
    u = url.lower()
    if "/api/" in u and ("search" in u or "cars" in u or "listing" in u):
        return "search"
    if "availabilit" in u or "calendar" in u or "pricing" in u:
        return "calendar"
    return None


@dataclass
class Collector:
    zone: str
    categorie: str
    snapshot_ts: str
    captured: dict = field(default_factory=lambda: {"search": [], "calendar": []})

    def on_response(self, resp: Response):
        kind = _looks_like_api(resp.url)
        if not kind:
            return
        try:
            data = resp.json()
        except Exception:
            return
        self.captured[kind].append(data)
        if DEBUG_DUMP:
            d = Path("debug"); d.mkdir(exist_ok=True)
            (d / f"{kind}_{int(time.time()*1000)}.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2))


# --------------------------- PARSING ----------------------------------------
# ⚠️ Ces deux fonctions dépendent de la STRUCTURE réelle des réponses/DOM.
# Squelette : adapte les clés une fois que tu as un dump réel (DEBUG_DUMP=True).

def parse_search(captured_search: list, page, zone, categorie, ts) -> list[dict]:
    rows = []
    # (A) via API captée
    for payload in captured_search:
        for car in _iter_cars(payload):
            rows.append(dict(
                listing_id=str(car.get("id")),
                snapshot_ts=ts, zone=zone, categorie=categorie,
                prix_jour_eur=_num(car.get("daily_price") or car.get("price_per_day")),
                nb_avis=_int(car.get("ratings_count") or car.get("reviews_count")),
                note_moyenne=_num(car.get("ratings_average") or car.get("rating")),
                still_listed=1,
                url=_abs(car.get("url") or car.get("path"))))
    # (B) fallback DOM si rien capté
    if not rows:
        cards = page.query_selector_all('[data-testid="car-card"], a[href*="/location-voiture/"]')
        seen = set()
        for c in cards:
            href = c.get_attribute("href") or ""
            lid = _id_from_url(href)
            if not lid or lid in seen:
                continue
            seen.add(lid)
            rows.append(dict(listing_id=lid, snapshot_ts=ts, zone=zone,
                             categorie=categorie, prix_jour_eur=None,
                             nb_avis=None, note_moyenne=None, still_listed=1,
                             url=_abs(href)))
    return rows


def parse_calendar(captured_cal: list, listing_id, ts) -> list[dict]:
    rows = []
    for payload in captured_cal:
        for day in _iter_days(payload):
            rows.append(dict(
                listing_id=str(listing_id), snapshot_ts=ts,
                target_date=str(day.get("date"))[:10],
                available=1 if _day_available(day) else 0,
                price_day_eur=_num(day.get("price") or day.get("daily_price"))))
    return rows


# petits utilitaires tolérants
def _iter_cars(p):
    for k in ("cars", "results", "listings", "data", "items", "hits"):
        v = p.get(k) if isinstance(p, dict) else None
        if isinstance(v, list):
            return v
    return p if isinstance(p, list) else []
def _iter_days(p):
    for k in ("calendar", "availabilities", "days", "data", "pricing"):
        v = p.get(k) if isinstance(p, dict) else None
        if isinstance(v, list):
            return v
    return p if isinstance(p, list) else []
def _day_available(d):
    if "available" in d: return bool(d["available"])
    if "allotment" in d and d["allotment"] is not None: return d["allotment"] > 0
    return str(d.get("status", "")).lower() == "available"
def _num(x):
    try: return float(str(x).replace("€", "").replace(",", ".").strip())
    except Exception: return None
def _int(x):
    try: return int(x)
    except Exception: return None
def _abs(u): return u if not u else (u if u.startswith("http") else BASE + u)
def _id_from_url(u):
    if not u: return None
    tail = u.rstrip("/").split("-")[-1]
    return tail if tail.isdigit() else None


# --------------------------- ORCHESTRATION ----------------------------------
def build_search_url(zone_cfg, categorie, start: dt.date, end: dt.date) -> str:
    """⚠️ À CALER sur la vraie URL de recherche du site (params réels)."""
    q = urllib.parse.urlencode(dict(
        latitude=zone_cfg["lat"], longitude=zone_cfg["lng"],
        start_date=start.isoformat(), end_date=end.isoformat(),
        car_type=categorie.lower()))
    return f"{BASE}/location-voiture/search?{q}"


def build_calendar_url(listing_id, start: dt.date, end: dt.date) -> str:
    """⚠️ À CALER : souvent un endpoint /api/.../cars/<id>/availabilities."""
    q = urllib.parse.urlencode(dict(start_date=start.isoformat(),
                                    end_date=end.isoformat()))
    return f"{BASE}/api/v1/cars/{listing_id}/availabilities?{q}"


def run_once():
    ts = dt.datetime.now().replace(microsecond=0).isoformat()
    today = dt.date.today()
    win_start = today + dt.timedelta(days=3)          # ex: prochaine semaine
    win_end = win_start + dt.timedelta(days=1)
    cal_start, cal_end = today, today + dt.timedelta(days=CALENDAR_HORIZON_DAYS)

    if not check_robots("/location-voiture/"):
        print("Interdit par robots.txt — arrêt. Vérifie/ajuste avant de relancer.")
        return

    con = sqlite3.connect(DB_PATH); init_db(con)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS)
        ctx = browser.new_context(user_agent=USER_AGENT, locale="fr-FR")
        page = ctx.new_page()

        for zone, cfg in ZONES.items():
            for cat in CATEGORIES:
                col = Collector(zone, cat, ts)
                page.on("response", col.on_response)
                url = build_search_url(cfg, cat, win_start, win_end)
                try:
                    _goto_guarded(page, url)
                except RateLimited:
                    print("Rate-limit persistant — on stoppe proprement.")
                    con.close(); browser.close(); return

                rows = parse_search(col.captured["search"], page, zone, cat, ts)
                _upsert(con, "listing_snapshots", rows)
                con.execute("INSERT INTO run_log VALUES(?,?,?,?,?)",
                            (ts, zone, cat, len(rows), "ok"))
                con.commit()
                print(f"[{zone} / {cat}] {len(rows)} annonces")
                page.remove_listener("response", col.on_response)

                # calendrier par annonce (le signal temporel le plus précieux)
                for r in rows:
                    lid = r["listing_id"]
                    cc = Collector(zone, cat, ts)
                    page.on("response", cc.on_response)
                    try:
                        _goto_guarded(page, build_calendar_url(lid, cal_start, cal_end))
                    except RateLimited:
                        print("Rate-limit — arrêt propre pendant calendrier.")
                        con.close(); browser.close(); return
                    cal_rows = parse_calendar(cc.captured["calendar"], lid, ts)
                    _upsert(con, "calendar_snapshots", cal_rows)
                    con.commit()
                    page.remove_listener("response", cc.on_response)
                    polite_sleep()
                polite_sleep()

        browser.close()
    con.close()
    print(f"\nSnapshot {ts} terminé. Relance ce script régulièrement (cron).")


def _goto_guarded(page, url):
    for attempt in range(MAX_BACKOFF_RETRIES + 1):
        page.goto(url, wait_until="networkidle", timeout=45000)
        polite_sleep()
        if not _is_rate_limited(page):
            return
        wait = BACKOFF_BASE * (2 ** attempt)
        print(f"[rate-limit] pause {wait:.0f}s (tentative {attempt+1})")
        time.sleep(wait)
    raise RateLimited(url)


def _upsert(con, table, rows):
    if not rows:
        return
    cols = list(rows[0].keys())
    ph = ",".join("?" * len(cols))
    con.executemany(
        f"INSERT OR REPLACE INTO {table}({','.join(cols)}) VALUES({ph})",
        [tuple(r[c] for c in cols) for r in rows])


if __name__ == "__main__":
    run_once()
