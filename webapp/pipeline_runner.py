"""
webapp/pipeline_runner.py — orchestration du pipeline en sous-processus
========================================================================

Enchaîne les 5 scripts CLI existants exactement comme on les a lancés à la main :
ingest -> analyze -> pipeline -> export_ml -> ml. Ce module ne touche PAS à git
(aucun commit/push) : c'est le rôle exclusif du workflow GitHub Actions déjà en
place. Il se contente d'exécuter localement et de republier les logs en direct
pour le dashboard (SSE, voir server.py).

Un seul run actif à la fois (état global en mémoire) : suffisant pour un usage
local mono-utilisateur.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp.data import invalidate_cache

STEP_PENDING, STEP_RUNNING, STEP_DONE, STEP_ERROR = "pending", "running", "done", "error"
RUN_IDLE, RUN_RUNNING, RUN_DONE, RUN_ERROR = "idle", "running", "done", "error"

# étapes du pipeline, dans l'ordre — mêmes scripts que ceux lancés manuellement
STEPS = [
    ("ingest", "Collecte GBFS", "ingest.py"),
    ("analyze", "Rapport marché", "analyze.py"),
    ("pipeline", "Scoring rentabilité", "pipeline.py"),
    ("export_ml", "Export ML", "export_ml.py"),
    ("ml", "Modèle de demande", "ml.py"),
]


class PipelineRun:
    def __init__(self) -> None:
        self.run_id = str(uuid.uuid4())
        self.status = RUN_RUNNING
        self.steps = [{"id": sid, "label": label, "status": STEP_PENDING}
                      for sid, label, _ in STEPS]
        self.logs: list[str] = []
        self.started_at = time.time()
        self.finished_at: float | None = None
        self.error: str | None = None
        self._subscribers: list[asyncio.Queue] = []

    # -- pub/sub pour le streaming SSE -----------------------------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        for ev in self._replay():           # rattrape un client qui arrive en cours de route
            q.put_nowait(ev)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    def _replay(self) -> list[dict]:
        events = [{"type": "steps", "steps": self.steps}]
        events += [{"type": "log", "line": l} for l in self.logs]
        if self.status in (RUN_DONE, RUN_ERROR):
            events.append({"type": self.status, "error": self.error})
        return events

    def _emit(self, event: dict) -> None:
        for q in list(self._subscribers):
            q.put_nowait(event)

    def _set_step(self, step_id: str, status: str) -> None:
        for s in self.steps:
            if s["id"] == step_id:
                s["status"] = status
        self._emit({"type": "steps", "steps": self.steps})

    def _log(self, line: str) -> None:
        self.logs.append(line)
        self._emit({"type": "log", "line": line})

    # -- exécution ----------------------------------------------------------
    async def execute(self) -> None:
        # nécessaire sous Windows : le crash "UnicodeEncodeError cp1252" observé
        # en lançant pipeline.py/ml.py depuis un terminal PowerShell classique.
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        try:
            for sid, label, script in STEPS:
                self._set_step(sid, STEP_RUNNING)
                self._log(f"── {label} ({script}) ──")
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, str(ROOT / script),
                    cwd=str(ROOT), env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                assert proc.stdout is not None
                async for raw in proc.stdout:
                    self._log(raw.decode("utf-8", errors="replace").rstrip())
                code = await proc.wait()
                if code != 0:
                    self._set_step(sid, STEP_ERROR)
                    self.status = RUN_ERROR
                    self.error = f"{label} a échoué (code {code})"
                    self._log(f"✖ {self.error}")
                    self._emit({"type": "error", "error": self.error})
                    return
                self._set_step(sid, STEP_DONE)

            self.status = RUN_DONE
            self._log("✔ Pipeline terminé.")
            invalidate_cache()
            self._emit({"type": "done"})
        except Exception as exc:                       # sécurité : ne jamais planter le serveur
            self.status = RUN_ERROR
            self.error = str(exc)
            self._log(f"✖ Erreur inattendue : {exc}")
            self._emit({"type": "error", "error": self.error})
        finally:
            self.finished_at = time.time()


_current: PipelineRun | None = None


def start_run() -> PipelineRun:
    """Démarre un run si aucun n'est actif ; sinon renvoie celui en cours
    (pas de double exécution concurrente)."""
    global _current
    if _current is not None and _current.status == RUN_RUNNING:
        return _current
    run = PipelineRun()
    _current = run
    asyncio.create_task(run.execute())
    return run


def get_run(run_id: str) -> PipelineRun | None:
    if _current is not None and _current.run_id == run_id:
        return _current
    return None


def status_snapshot() -> dict:
    if _current is None:
        return {"run_id": None, "status": RUN_IDLE,
                "steps": [{"id": sid, "label": label, "status": STEP_PENDING}
                         for sid, label, _ in STEPS],
                "error": None}
    return {"run_id": _current.run_id, "status": _current.status,
            "steps": _current.steps, "error": _current.error}
