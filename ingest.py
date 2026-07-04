"""
ingest.py — Un passage de collecte (à lancer en boucle via le planificateur)
============================================================================

À CHAQUE exécution : prend une photo horodatée de la flotte GBFS et l'AJOUTE à
une base append-only. C'est la RÉPÉTITION dans le temps qui crée le signal :
comme les voitures louées disparaissent du flux, une location se lit comme un
`listing_id` présent puis absent d'un passage à l'autre (voir features.py).

Cadence recommandée : ~toutes les 20 min (sinon on rate les locations courtes).
Voir README pour la planification Windows (Planificateur de tâches).

Tables (append-only) :
  vehicle_snapshots  : 1 ligne par (snapshot_ts, système, véhicule)
  vehicle_types      : référentiel modèle/année/motorisation (upsert)
  pricing_plans      : tarifs par plan à chaque passage (historisé)
  run_log            : trace de chaque passage
"""
from __future__ import annotations
import csv
import sqlite3
import datetime as dt

from config import DB_PATH, DATA_DIR, SYSTEMS, IDF_BBOX
import gbfs

# colonnes stockées par véhicule/passage (ordre canonique, réutilisé CSV+SQL)
SNAPSHOT_COLS = [
    "snapshot_ts", "system_id", "listing_id", "vehicle_id", "commune",
    "lat", "lon", "is_reserved", "is_disabled", "current_range_meters",
    "vehicle_type_id", "make", "model", "year", "propulsion",
    "pricing_plan_id", "hourly_rate", "daily_rate", "rental_url",
]
RUNLOG_COLS = ["snapshot_ts", "system_id", "n_seen", "n_kept",
               "n_reserved", "status", "note"]


SCHEMA = """
CREATE TABLE IF NOT EXISTS vehicle_snapshots(
    snapshot_ts TEXT NOT NULL,
    system_id   TEXT NOT NULL,
    listing_id  TEXT,
    vehicle_id  TEXT NOT NULL,
    commune     TEXT,
    lat REAL, lon REAL,
    is_reserved INTEGER, is_disabled INTEGER,
    current_range_meters INTEGER,
    vehicle_type_id TEXT,
    make TEXT, model TEXT, year INTEGER, propulsion TEXT,
    pricing_plan_id TEXT,
    hourly_rate REAL, daily_rate REAL,
    rental_url TEXT,
    PRIMARY KEY(snapshot_ts, system_id, vehicle_id)
);
CREATE INDEX IF NOT EXISTS ix_snap_listing ON vehicle_snapshots(listing_id, snapshot_ts);
CREATE INDEX IF NOT EXISTS ix_snap_ts      ON vehicle_snapshots(snapshot_ts);

CREATE TABLE IF NOT EXISTS vehicle_types(
    vehicle_type_id TEXT PRIMARY KEY,
    make TEXT, model TEXT, year INTEGER, propulsion TEXT,
    form_factor TEXT, name TEXT, updated_ts TEXT
);

CREATE TABLE IF NOT EXISTS pricing_plans(
    plan_id TEXT, snapshot_ts TEXT,
    hourly_rate REAL, daily_rate REAL, currency TEXT,
    PRIMARY KEY(plan_id, snapshot_ts)
);

CREATE TABLE IF NOT EXISTS run_log(
    snapshot_ts TEXT, system_id TEXT,
    n_seen INTEGER, n_kept INTEGER, n_reserved INTEGER,
    status TEXT, note TEXT
);
"""


def init_db(con: sqlite3.Connection):
    con.executescript(SCHEMA)
    con.commit()


def _in_idf(lat, lon) -> bool:
    if lat is None or lon is None:
        return False
    b = IDF_BBOX
    return (b["lat_min"] <= lat <= b["lat_max"]
            and b["lon_min"] <= lon <= b["lon_max"])


def run_snapshot(con: sqlite3.Connection | None = None) -> str:
    """Collecte tous les SYSTEMS, déduplique par listing_id, filtre IDF, stocke."""
    # horodatage en UTC naïf (cohérent entre ta machine et les runners GitHub)
    ts = dt.datetime.now(dt.timezone.utc).replace(microsecond=0,
                                                  tzinfo=None).isoformat()
    own_con = con is None
    if own_con:
        con = sqlite3.connect(DB_PATH)
    init_db(con)

    seen_listings: set[str] = set()
    total_kept = 0

    for system in SYSTEMS:
        try:
            rows = gbfs.collect_system(system)
        except Exception as e:                       # collecte d'un système KO
            runlog = (ts, system, 0, 0, 0, "error", str(e)[:300])
            con.execute("INSERT INTO run_log VALUES(?,?,?,?,?,?,?)", runlog)
            con.commit()
            _append_runlog_csv(runlog)
            print(f"[{system}] ERREUR : {e}")
            continue

        kept = []
        for r in rows:
            if not _in_idf(r["lat"], r["lon"]):
                continue
            key = r["listing_id"] or r["vehicle_id"]   # dédup inter-systèmes
            if key in seen_listings:
                continue
            seen_listings.add(key)
            r["snapshot_ts"] = ts
            kept.append(r)

        _store_snapshots(con, kept)                    # sqlite (miroir local)
        _store_types(con, kept, ts)
        _store_pricing(con, system, ts)               # plans complets du système
        _append_csv(kept, ts)                          # CSV canonique (committé)
        n_res = sum(r["is_reserved"] for r in kept)
        runlog = (ts, system, len(rows), len(kept), n_res, "ok", None)
        con.execute("INSERT INTO run_log VALUES(?,?,?,?,?,?,?)", runlog)
        con.commit()
        _append_runlog_csv(runlog)
        total_kept += len(kept)
        print(f"[{system}] {len(rows)} vus -> {len(kept)} gardés (IDF, dédup), "
              f"{n_res} réservés")

    if own_con:
        con.close()
    print(f"Snapshot {ts} : {total_kept} véhicules stockés dans {DB_PATH.name}")
    return ts


def _append_csv(rows, ts):
    """Append les véhicules du passage dans data/AAAA-MM-JJ.csv (canonique)."""
    if not rows:
        return
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / f"{ts[:10]}.csv"               # partition par jour
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=SNAPSHOT_COLS)
        if new:
            w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c) for c in SNAPSHOT_COLS})


def _append_runlog_csv(runlog):
    """Append une ligne dans data/runs.csv (grille autoritative des passages)."""
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / "runs.csv"
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(RUNLOG_COLS)
        w.writerow(runlog)


def _store_snapshots(con, rows):
    if not rows:
        return
    cols = ["snapshot_ts", "system_id", "listing_id", "vehicle_id", "commune",
            "lat", "lon", "is_reserved", "is_disabled", "current_range_meters",
            "vehicle_type_id", "make", "model", "year", "propulsion",
            "pricing_plan_id", "hourly_rate", "daily_rate", "rental_url"]
    ph = ",".join("?" * len(cols))
    con.executemany(
        f"INSERT OR REPLACE INTO vehicle_snapshots({','.join(cols)}) VALUES({ph})",
        [tuple(r.get(c) for c in cols) for r in rows])


def _store_types(con, rows, ts):
    seen = {}
    for r in rows:
        tid = r.get("vehicle_type_id")
        if tid and tid not in seen:
            seen[tid] = (tid, r.get("make"), r.get("model"), r.get("year"),
                         r.get("propulsion"), None, None, ts)
    if seen:
        con.executemany(
            "INSERT OR REPLACE INTO vehicle_types"
            "(vehicle_type_id,make,model,year,propulsion,form_factor,name,updated_ts)"
            " VALUES(?,?,?,?,?,?,?,?)", list(seen.values()))


def _store_pricing(con, system, ts):
    """Historise tous les plans tarifaires du système à ce passage."""
    try:
        feeds = gbfs.discover_feeds(system)
        plans = gbfs.parse_pricing_plans(gbfs.fetch_json(feeds["system_pricing_plans"]))
    except Exception:
        return
    con.executemany(
        "INSERT OR REPLACE INTO pricing_plans(plan_id,snapshot_ts,hourly_rate,"
        "daily_rate,currency) VALUES(?,?,?,?,?)",
        [(pid, ts, p.get("hourly_rate"), p.get("daily_rate"), p.get("currency"))
         for pid, p in plans.items()])


if __name__ == "__main__":
    run_snapshot()
