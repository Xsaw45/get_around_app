"""
bootstrap_from_dataset.py
=========================
Injecte ta collecte existante (onglet 'annonces' du dataset Cowork) comme
PREMIER snapshot dans la base temporelle. Tu ne repars pas de zéro : le 4 juillet
devient t0, et chaque run futur du scraper s'y ajoute.

Usage : python bootstrap_from_dataset.py GETARO_1.XLS
"""
import sys, sqlite3
import pandas as pd
from scraper import init_db, DB_PATH

def main(xls_path):
    df = pd.read_excel(xls_path, sheet_name="annonces")
    # ne garder qu'une ligne par annonce (la fenêtre 'semaine_1j' de préférence)
    df = df[df["fenetre"] == "semaine_1j"].copy()
    df["listing_id"] = df["url_annonce"].astype(str).str.rstrip("/").str.split("-").str[-1]
    df = df[df["listing_id"].str.isdigit()]
    ts = str(df["date_collecte"].iloc[0])  # '2026-07-04' -> t0

    rows = df.assign(snapshot_ts=ts, still_listed=1).rename(columns={
        "zone_recherche": "zone", "categorie": "categorie",
        "prix_jour_eur": "prix_jour_eur", "nb_avis": "nb_avis",
        "note_moyenne": "note_moyenne", "url_annonce": "url"})[[
        "listing_id", "snapshot_ts", "zone", "categorie", "prix_jour_eur",
        "nb_avis", "note_moyenne", "still_listed", "url"]]

    con = sqlite3.connect(DB_PATH); init_db(con)
    con.executemany(
        "INSERT OR REPLACE INTO listing_snapshots "
        "(listing_id,snapshot_ts,zone,categorie,prix_jour_eur,nb_avis,"
        "note_moyenne,still_listed,url) VALUES(?,?,?,?,?,?,?,?,?)",
        rows.itertuples(index=False, name=None))
    con.commit()
    print(f"t0 = {ts} : {len(rows)} annonces injectées dans {DB_PATH}")
    print("NB : pas de calendrier pour t0 (Cowork n'a pas collecté les dispos).")
    print("Le signal réservation démarrera au 1er run du scraper.")
    con.close()

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "GETARO_1.XLS")
