"""
test_features.py — Vérifie le moteur temporel (détection de location).

Lançable directement (sans pytest) :  python tests/test_features.py
Compatible pytest aussi :             pytest tests/
"""
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import features as f


def _snap(specs, times):
    """specs = {uid: [présence 0/1 à chaque passage]} ; times = liste d'heures.

    Retourne (df, grid). La grille = TOUS les passages (comme run_log), pas
    seulement ceux où un véhicule était présent — c'est ce qui rend la détection
    correcte même avec un seul véhicule."""
    ts = pd.to_datetime(times)
    rows = []
    for uid, pres in specs.items():
        for t, p in zip(ts, pres):
            if p:
                rows.append(dict(snapshot_ts=t, listing_id=uid, vehicle_id=uid,
                                 commune="paris", make="Renault", model="Clio",
                                 year=2020, propulsion="combustion",
                                 daily_rate=50.0, hourly_rate=6.0))
    df = pd.DataFrame(rows)
    df["uid"] = df["listing_id"]
    return df, ts.values


TIMES = ["2026-07-04T10:00", "2026-07-04T11:00",
         "2026-07-04T12:00", "2026-07-04T13:00"]


def test_rental_detected_and_duration():
    # A disparaît 2 passages puis revient -> 1 location de 3 h
    snap, grid = _snap({"A": [1, 0, 0, 1]}, TIMES)
    ep = f.rental_episodes(snap, grid=grid)
    assert len(ep) == 1, "une location attendue"
    assert ep.iloc[0]["duration_h"] == 3.0
    assert ep.iloc[0]["gap_snapshots"] == 2


def test_always_present_has_no_rental():
    snap, grid = _snap({"B": [1, 1, 1, 1]}, TIMES)
    assert f.rental_episodes(snap, grid=grid).empty


def test_trailing_absence_is_censored():
    # C absent en fin de série (jamais réapparu) -> aucun épisode borné
    snap, grid = _snap({"C": [1, 1, 0, 0]}, TIMES)
    assert f.rental_episodes(snap, grid=grid).empty


def test_min_absence_filters_short_gaps():
    # trou d'un seul passage, ignoré si min_absence_snapshots=2
    snap, grid = _snap({"D": [1, 0, 1, 1]}, TIMES)
    assert len(f.rental_episodes(snap, min_absence_snapshots=1, grid=grid)) == 1
    assert f.rental_episodes(snap, min_absence_snapshots=2, grid=grid).empty


def test_occupation_rate():
    snap, grid = _snap({"A": [1, 0, 0, 1]}, TIMES)
    util = f.utilization_per_vehicle(snap, grid=grid)
    row = util[util["uid"] == "A"].iloc[0]
    assert row["n_passages"] == 4
    assert row["n_absent"] == 2
    assert row["taux_occupation"] == 0.5
    assert row["n_episodes"] == 1


def test_two_separate_rentals():
    # E: présent, absent, présent, absent, présent -> 2 locations distinctes
    times = TIMES + ["2026-07-04T14:00"]
    snap, grid = _snap({"E": [1, 0, 1, 0, 1]}, times)
    assert len(f.rental_episodes(snap, grid=grid)) == 2


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  OK   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passés.")
    sys.exit(1 if failed else 0)
