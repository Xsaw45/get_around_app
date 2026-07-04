"""
gbfs.py — Client GBFS Getaround (lecture pure, aucune écriture)
==============================================================

Rôle : récupérer, pour un système donné (ex. "paris"), les 4 flux GBFS v3 et
les rendre sous une forme normalisée et prête à stocker, en résolvant déjà les
jointures (type de véhicule + plan tarifaire) pour chaque véhicule.

Aucune dépendance lourde : urllib de la stdlib suffit (le GBFS est du JSON
public). Le seul "réseau" du projet vit ici.
"""
from __future__ import annotations
import json
import re
import time
import urllib.request
import urllib.error

from config import (GBFS_SYSTEM_TMPL, USER_AGENT, HTTP_TIMEOUT,
                    HTTP_RETRIES, HTTP_BACKOFF)

# extrait l'id d'annonce P2P stable depuis .../citroen-jumper-261942
_LISTING_ID_RE = re.compile(r"-(\d+)/?$")
# extrait le slug de commune depuis /location-voiture/colombes/...
_COMMUNE_RE = re.compile(r"/location-voiture/([^/]+)/")
# extrait l'année depuis "Citroen Jumper (2011)"
_YEAR_RE = re.compile(r"\((\d{4})\)")


# --------------------------------------------------------------------------
# HTTP bas niveau, avec retry/backoff
# --------------------------------------------------------------------------
def fetch_json(url: str) -> dict:
    last_err = None
    for attempt in range(HTTP_RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = e
            if attempt < HTTP_RETRIES - 1:
                time.sleep(HTTP_BACKOFF * (2 ** attempt))
    raise RuntimeError(f"GBFS injoignable après {HTTP_RETRIES} essais : {url} ({last_err})")


# --------------------------------------------------------------------------
# Découverte des feeds d'un système
# --------------------------------------------------------------------------
def discover_feeds(system: str) -> dict[str, str]:
    """{nom_feed: url} pour un système (ex. 'paris')."""
    disco = fetch_json(GBFS_SYSTEM_TMPL.format(system=system))
    feeds = disco["data"]["feeds"]
    return {f["name"]: f["url"] for f in feeds}


# --------------------------------------------------------------------------
# Normalisation des flux de référence (type véhicule, plan tarifaire)
# --------------------------------------------------------------------------
def _localized(val) -> str | None:
    """GBFS v3 : un champ texte peut être une string OU une liste
    [{text, language}]. On garde le français si présent, sinon le 1er."""
    if isinstance(val, list):
        if not val:
            return None
        return next((x["text"] for x in val if x.get("language") == "fr"),
                    val[0].get("text"))
    return val


def parse_vehicle_types(payload: dict) -> dict[str, dict]:
    """type_id -> {make, model, year, propulsion, form_factor, name}."""
    out = {}
    for t in payload["data"]["vehicle_types"]:
        name = _localized(t.get("name"))
        year = None
        if name:
            m = _YEAR_RE.search(name)
            if m:
                year = int(m.group(1))
        out[t["vehicle_type_id"]] = dict(
            make=_localized(t.get("make")), model=_localized(t.get("model")),
            year=year, propulsion=t.get("propulsion_type"),
            form_factor=t.get("form_factor"), name=name)
    return out


def _daily_hourly_from_plan(plan: dict) -> tuple[float | None, float | None]:
    """Extrait (tarif_horaire, tarif_journalier) d'un plan GBFS per_min_pricing.

    Convention Getaround : deux tranches. La 1re a interval=60 (facturation
    horaire, rate = €/h). La 2e a interval=1440 (facturation journalière,
    rate = €/j).
    """
    hourly = daily = None
    for tr in plan.get("per_min_pricing", []):
        if tr.get("interval") == 60 and hourly is None:
            hourly = _num(tr.get("rate"))
        elif tr.get("interval") == 1440 and daily is None:
            daily = _num(tr.get("rate"))
    return hourly, daily


def parse_pricing_plans(payload: dict) -> dict[str, dict]:
    """plan_id -> {hourly_rate, daily_rate, currency}."""
    out = {}
    for p in payload["data"]["plans"]:
        hourly, daily = _daily_hourly_from_plan(p)
        out[p["plan_id"]] = dict(hourly_rate=hourly, daily_rate=daily,
                                 currency=p.get("currency"))
    return out


# --------------------------------------------------------------------------
# Collecte complète d'un système : véhicules enrichis type + prix
# --------------------------------------------------------------------------
def collect_system(system: str) -> list[dict]:
    """Retourne une ligne normalisée par véhicule (type + prix déjà résolus)."""
    feeds = discover_feeds(system)
    types = parse_vehicle_types(fetch_json(feeds["vehicle_types"]))
    plans = parse_pricing_plans(fetch_json(feeds["system_pricing_plans"]))
    vehicles = fetch_json(feeds["vehicle_status"])["data"]["vehicles"]

    rows = []
    for v in vehicles:
        web = (v.get("rental_uris") or {}).get("web", "") or ""
        t = types.get(v.get("vehicle_type_id"), {})
        p = plans.get(v.get("pricing_plan_id"), {})
        rows.append(dict(
            system_id=system,
            listing_id=_listing_id(web),
            vehicle_id=v.get("vehicle_id"),
            commune=_commune(web),
            lat=v.get("lat"), lon=v.get("lon"),
            is_reserved=int(bool(v.get("is_reserved"))),
            is_disabled=int(bool(v.get("is_disabled"))),
            current_range_meters=v.get("current_range_meters"),
            vehicle_type_id=v.get("vehicle_type_id"),
            make=t.get("make"), model=t.get("model"), year=t.get("year"),
            propulsion=t.get("propulsion"),
            pricing_plan_id=v.get("pricing_plan_id"),
            hourly_rate=p.get("hourly_rate"), daily_rate=p.get("daily_rate"),
            rental_url=web,
        ))
    return rows


# --------------------------------------------------------------------------
# petits utilitaires
# --------------------------------------------------------------------------
def _listing_id(url: str) -> str | None:
    m = _LISTING_ID_RE.search(url or "")
    return m.group(1) if m else None


def _commune(url: str) -> str | None:
    m = _COMMUNE_RE.search(url or "")
    return m.group(1) if m else None


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None
