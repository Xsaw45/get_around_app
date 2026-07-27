"""
webapp/server.py — dashboard local (FastAPI)
=============================================

Sert l'interface web du dashboard et expose une petite API :
  GET  /                              page du dashboard
  POST /api/pipeline/run              démarre le pipeline complet (ingest -> ... -> ml)
  GET  /api/pipeline/stream/{run_id}  logs + étapes en direct (SSE)
  GET  /api/pipeline/status           dernier état connu (reconnexion)
  GET  /api/market/summary            photo de marché (communes, prix, marques...)
  GET  /api/rentability?by=segment|model
  GET  /api/ml/insights               importance des variables, dépendances partielles
  GET  /api/activity                  passages de collecte par jour
  GET  /api/fleet/points              position + segment de chaque véhicule (carte)
  POST /api/simulate                  rentabilité d'un véhicule hypothétique à un endroit donné
  GET  /api/simulate/best             classement des modèles réels les plus rentables à un endroit donné

Usage local uniquement pour l'instant (bind 127.0.0.1) :
    python -m webapp.server
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from webapp import data
from webapp import pipeline_runner
from webapp import simulate as simulate_mod

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Getaround IDF — dashboard")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
@app.post("/api/pipeline/run")
async def run_pipeline():
    run = pipeline_runner.start_run()
    return {"run_id": run.run_id, "status": run.status}


@app.get("/api/pipeline/status")
def pipeline_status():
    return pipeline_runner.status_snapshot()


@app.get("/api/pipeline/stream/{run_id}")
async def pipeline_stream(run_id: str):
    run = pipeline_runner.get_run(run_id)
    if run is None:
        raise HTTPException(404, "run inconnu (terminé depuis trop longtemps ou jamais démarré)")

    async def events():
        q = run.subscribe()
        try:
            while True:
                event = await q.get()
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event["type"] in ("done", "error"):
                    break
        finally:
            run.unsubscribe(q)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------------------
# Données pour les graphiques
# ---------------------------------------------------------------------------
@app.get("/api/collector/status")
def collector_status():
    return data.collector_status()


@app.get("/api/market/summary")
def market_summary():
    return data.market_summary()


@app.get("/api/rentability")
def rentability(by: str = "segment"):
    if by not in ("segment", "model"):
        raise HTTPException(400, "by doit être 'segment' ou 'model'")
    return data.rentability(by)


@app.get("/api/ml/insights")
def ml_insights():
    return data.ml_insights()


@app.get("/api/activity")
def activity():
    return data.activity()


@app.get("/api/fleet/points")
def fleet_points():
    return data.fleet_points()


class SimulateRequest(BaseModel):
    make: str
    model: str
    propulsion: str = "hybrid"
    lat: float
    lon: float
    radius_km: float = 1.2
    daily_rate: float | None = None


@app.post("/api/simulate")
def simulate(req: SimulateRequest):
    result = simulate_mod.simulate(req.make, req.model, req.propulsion, req.lat,
                                   req.lon, req.radius_km, req.daily_rate)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.get("/api/simulate/best")
def simulate_best(lat: float, lon: float, radius_km: float = 1.2, top_n: int = 15):
    result = simulate_mod.rank_models_for_location(lat, lon, radius_km, top_n)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


if __name__ == "__main__":
    import uvicorn

    if sys.platform == "win32":
        # nécessaire pour que asyncio.create_subprocess_exec fonctionne (le
        # pipeline est lancé en sous-processus depuis pipeline_runner.py)
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    uvicorn.run(app, host="127.0.0.1", port=8000)
