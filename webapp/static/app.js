// Dashboard Getaround IDF — fetch API + rendu Chart.js + suivi pipeline (SSE)

const DARK = window.matchMedia("(prefers-color-scheme: dark)").matches;

// palette catégorielle (ordre fixe, voir skill dataviz) — huit teintes validées CVD
const SLOT = DARK
  ? ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
  : ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"];

// même ordre que pipeline.py:_SEG_RULES -> couleur d'un segment cohérente sur tous les graphs
const SEGMENT_ORDER = ["Citadine", "Utilitaire", "SUV", "Berline", "Minibus", "Familiale", "Autre", "Cabriolet"];
function segmentColor(seg) {
  const i = SEGMENT_ORDER.indexOf(seg);
  return SLOT[i >= 0 ? i : 0];
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

Chart.defaults.font.family = "system-ui, -apple-system, 'Segoe UI', sans-serif";
Chart.defaults.color = cssVar("--text-secondary");
Chart.defaults.borderColor = cssVar("--grid");

// ---------------------------------------------------------------------------
// Helpers graphiques
// ---------------------------------------------------------------------------
const charts = {};
function upsertChart(id, config) {
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart(document.getElementById(id).getContext("2d"), config);
}

function barConfig(labels, values, { horizontal = true, color = SLOT[0] } = {}) {
  return {
    type: "bar",
    data: { labels, datasets: [{ data: values, backgroundColor: color, borderRadius: 4, maxBarThickness: 22 }] },
    options: {
      indexAxis: horizontal ? "y" : "x",
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { color: cssVar("--grid") }, ticks: { color: cssVar("--text-muted") }, beginAtZero: true },
        y: { grid: { display: false }, ticks: { color: cssVar("--text-secondary") } },
      },
    },
  };
}

function lineConfig(points, xLabel) {
  return {
    type: "line",
    data: {
      labels: points.map((p) => Math.round(p.x)),
      datasets: [{ data: points.map((p) => p.y * 100), borderColor: SLOT[0], backgroundColor: SLOT[0],
                  tension: 0.25, pointRadius: 3, borderWidth: 2 }],
    },
    options: {
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { display: false }, ticks: { color: cssVar("--text-muted") },
             title: { display: true, text: xLabel, color: cssVar("--text-muted") } },
        y: { grid: { color: cssVar("--grid") }, ticks: { color: cssVar("--text-muted"), callback: (v) => v + "%" },
             title: { display: true, text: "occupation", color: cssVar("--text-muted") } },
      },
    },
  };
}

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------------------
// Données
// ---------------------------------------------------------------------------
async function loadCollectorStatus() {
  try {
    const s = await fetchJSON("/api/collector/status");
    document.getElementById("collector-dot").className = "dot " + s.state;
    const age = s.age_hours != null ? ` (il y a ${s.age_hours} h)` : "";
    document.getElementById("collector-label").textContent = `Collecte auto : ${s.label}${age}`;
  } catch (e) { console.error(e); }
}

async function loadMarket() {
  const m = await fetchJSON("/api/market/summary");
  const statRow = document.getElementById("stat-row");
  if (m.empty) {
    statRow.innerHTML = '<p class="empty-note">Pas encore de données — lance le pipeline.</p>';
    return;
  }
  statRow.innerHTML = `
    <div class="stat"><div class="value">${m.n_vehicules}</div><div class="label">véhicules</div></div>
    <div class="stat"><div class="value">${m.n_communes}</div><div class="label">communes</div></div>
    <div class="stat"><div class="value">${m.n_passages}</div><div class="label">passages collectés</div></div>
    <div class="stat"><div class="value">${Math.round(m.prix.median)} €</div><div class="label">prix médian / jour</div></div>
  `;

  upsertChart("chart-communes", barConfig(m.par_commune.map((d) => d.label), m.par_commune.map((d) => d.value)));
  upsertChart("chart-marques", barConfig(m.par_marque.map((d) => d.label), m.par_marque.map((d) => d.value)));
  upsertChart("chart-motorisation", barConfig(m.par_motorisation.map((d) => d.label), m.par_motorisation.map((d) => d.value)));

  upsertChart("chart-prix", {
    type: "bar",
    data: { labels: m.prix_histogramme.map((d) => Math.round(d.x)),
            datasets: [{ data: m.prix_histogramme.map((d) => d.count), backgroundColor: SLOT[0], borderRadius: 2 }] },
    options: {
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { display: false }, ticks: { color: cssVar("--text-muted"), maxTicksLimit: 8 },
             title: { display: true, text: "€ / jour", color: cssVar("--text-muted") } },
        y: { grid: { color: cssVar("--grid") }, ticks: { color: cssVar("--text-muted") }, beginAtZero: true },
      },
    },
  });
}

async function loadRentability() {
  const seg = await fetchJSON("/api/rentability?by=segment");
  const labels = seg.map((d) => d.segment);
  const colors = labels.map(segmentColor);

  upsertChart("chart-roi-segment", {
    type: "bar",
    data: { labels, datasets: [{ data: seg.map((d) => d.roi_net), backgroundColor: colors, borderRadius: 4 }] },
    options: {
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { display: false }, ticks: { color: cssVar("--text-secondary") } },
        y: { grid: { color: cssVar("--grid") }, ticks: { color: cssVar("--text-muted") }, beginAtZero: true,
             title: { display: true, text: "ROI net", color: cssVar("--text-muted") } },
      },
    },
  });

  upsertChart("chart-occupation-segment", {
    type: "bar",
    data: { labels, datasets: [{ data: seg.map((d) => d.occupation_moy * 100), backgroundColor: colors, borderRadius: 4 }] },
    options: {
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { display: false }, ticks: { color: cssVar("--text-secondary") } },
        y: { grid: { color: cssVar("--grid") }, ticks: { color: cssVar("--text-muted"), callback: (v) => v + "%" },
             beginAtZero: true, title: { display: true, text: "occupation (%)", color: cssVar("--text-muted") } },
      },
    },
  });

  const models = await fetchJSON("/api/rentability?by=model");
  upsertChart("chart-top-modeles", barConfig(models.map((d) => d.model), models.map((d) => d.roi_net)));
}

async function loadML() {
  const ml = await fetchJSON("/api/ml/insights");
  const section = document.getElementById("ml-section");
  if (ml.empty) {
    section.innerHTML = '<p class="empty-note">Pas assez de passages collectés pour entraîner le modèle (2+ nécessaires).</p>';
    return;
  }

  const imp = [...ml.importance].sort((a, b) => a.value - b.value);
  upsertChart("chart-ml-importance", barConfig(imp.map((d) => d.feature), imp.map((d) => d.value)));

  const segs = Object.keys(ml.price_effect);
  upsertChart("chart-price-effect", {
    type: "line",
    data: {
      labels: ml.price_effect[segs[0]].map((p) => p.price),
      datasets: segs.map((seg) => ({
        label: seg,
        data: ml.price_effect[seg].map((p) => p.occupation * 100),
        borderColor: segmentColor(seg),
        backgroundColor: segmentColor(seg),
        tension: 0.25, pointRadius: 3, borderWidth: 2,
      })),
    },
    options: {
      plugins: { legend: { display: true, labels: { color: cssVar("--text-secondary") } } },
      scales: {
        x: { grid: { display: false }, ticks: { color: cssVar("--text-muted") },
             title: { display: true, text: "€ / jour", color: cssVar("--text-muted") } },
        y: { grid: { color: cssVar("--grid") }, ticks: { color: cssVar("--text-muted"), callback: (v) => v + "%" },
             title: { display: true, text: "occupation prédite", color: cssVar("--text-muted") } },
      },
    },
  });

  upsertChart("chart-pdp-price", lineConfig(ml.partial_dependence.daily_rate, "€ / jour"));
  upsertChart("chart-pdp-age", lineConfig(ml.partial_dependence.age, "âge (ans)"));
}

async function loadActivity() {
  const act = await fetchJSON("/api/activity");
  upsertChart("chart-activity", {
    type: "line",
    data: { labels: act.map((d) => d.date),
            datasets: [{ data: act.map((d) => d.passages), borderColor: SLOT[0], backgroundColor: SLOT[0],
                        tension: 0.2, pointRadius: 2, borderWidth: 2, fill: false }] },
    options: {
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { display: false }, ticks: { color: cssVar("--text-muted"), maxTicksLimit: 12 } },
        y: { grid: { color: cssVar("--grid") }, ticks: { color: cssVar("--text-muted") }, beginAtZero: true,
             title: { display: true, text: "passages / jour", color: cssVar("--text-muted") } },
      },
    },
  });
}

function loadAllData() {
  loadCollectorStatus();
  loadMarket().catch(console.error);
  loadRentability().catch(console.error);
  loadML().catch(console.error);
  loadActivity().catch(console.error);
}

// ---------------------------------------------------------------------------
// Pipeline : lancement + suivi en direct (SSE)
// ---------------------------------------------------------------------------
let currentEventSource = null;

function renderSteps(steps) {
  document.getElementById("steps").innerHTML =
    steps.map((s) => `<li class="${s.status}"><span class="ico"></span>${s.label}</li>`).join("");
}

function appendLog(line) {
  const pre = document.getElementById("log");
  pre.textContent += line + "\n";
  pre.scrollTop = pre.scrollHeight;
}

function setRunning(running) {
  const btn = document.getElementById("run-btn");
  btn.disabled = running;
  btn.textContent = running ? "Pipeline en cours…" : "Lancer le pipeline";
}

function attachStream(runId) {
  if (currentEventSource) currentEventSource.close();
  setRunning(true);
  const es = new EventSource(`/api/pipeline/stream/${runId}`);
  currentEventSource = es;
  es.onmessage = (ev) => {
    const evt = JSON.parse(ev.data);
    if (evt.type === "steps") renderSteps(evt.steps);
    else if (evt.type === "log") appendLog(evt.line);
    else if (evt.type === "done") {
      setRunning(false);
      es.close();
      loadAllData();
    } else if (evt.type === "error") {
      appendLog(`— erreur : ${evt.error} —`);
      setRunning(false);
      es.close();
    }
  };
  es.onerror = () => { setRunning(false); es.close(); };
}

document.getElementById("run-btn").addEventListener("click", async () => {
  document.getElementById("log").textContent = "";
  try {
    const res = await fetch("/api/pipeline/run", { method: "POST" });
    const run = await res.json();
    attachStream(run.run_id);
  } catch (e) {
    appendLog(`— impossible de démarrer le pipeline : ${e} —`);
  }
});

async function restoreRunState() {
  const status = await fetchJSON("/api/pipeline/status");
  renderSteps(status.steps);
  if (status.status === "running" && status.run_id) attachStream(status.run_id);
}

// ---------------------------------------------------------------------------
// Simulateur — carte + formulaire + résultat
// ---------------------------------------------------------------------------
let simMap, simMarker;
let simLatLon = { lat: 48.8467, lon: 2.3958 }; // place de la Nation, par défaut

function updatePosLabel() {
  document.getElementById("sim-pos-label").textContent =
    `${simLatLon.lat.toFixed(4)}, ${simLatLon.lon.toFixed(4)}`;
}

function initSimMap() {
  simMap = L.map("sim-map").setView([simLatLon.lat, simLatLon.lon], 14);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors", maxZoom: 18,
  }).addTo(simMap);

  simMarker = L.marker([simLatLon.lat, simLatLon.lon], { draggable: true }).addTo(simMap);
  simMarker.on("dragend", () => {
    const p = simMarker.getLatLng();
    simLatLon = { lat: p.lat, lon: p.lng };
    updatePosLabel();
  });
  simMap.on("click", (e) => {
    simMarker.setLatLng(e.latlng);
    simLatLon = { lat: e.latlng.lat, lon: e.latlng.lng };
    updatePosLabel();
  });

  fetchJSON("/api/fleet/points").then((pts) => {
    pts.forEach((p) => {
      L.circleMarker([p.lat, p.lon], { radius: 3, color: segmentColor(p.segment),
                                       weight: 0, fillOpacity: 0.55 }).addTo(simMap);
    });
  }).catch(console.error);
}

function capWarningHtml(r) {
  return r.cap_warning
    ? `<div class="warn-box"><span class="warn-dot"></span><span>${r.cap_warning}</span></div>`
    : "";
}

function verdictDotClass(rentable) {
  if (rentable === null || rentable === undefined) return "gray";
  return rentable ? "green" : "red";
}

function verdictBadge(rentable) {
  const label = rentable === null || rentable === undefined ? "indéterminé" : (rentable ? "rentable" : "pas rentable");
  return `<span class="dot ${verdictDotClass(rentable)}"></span>${label}`;
}

function ciSuffix(ci) {
  if (!ci) return "";
  return ` <span class="ci-note">[${(ci.low * 100).toFixed(1)}–${(ci.high * 100).toFixed(1)}%, n=${ci.n}]</span>`;
}

function renderSimResult(r) {
  const dimClass = r.verdict_reliable ? "" : "dimmed";
  const noteHtml = r.verdict_note ? `<p class="sub">${r.verdict_note}</p>` : "";
  document.getElementById("sim-result").innerHTML = `
    ${capWarningHtml(r)}
    <div class="stat-row">
      <div class="stat"><div class="value">${r.segment}</div><div class="label">segment détecté</div></div>
      <div class="stat"><div class="value">${(r.occupancy * 100).toFixed(1)}%</div><div class="label">occupation estimée${ciSuffix(r.occupancy_ci)}</div></div>
      <div class="stat"><div class="value">${r.daily_rate} €</div><div class="label">prix / jour utilisé</div></div>
      <div class="stat"><div class="value">${(r.breakeven_occupancy * 100).toFixed(1)}%</div><div class="label">seuil requis (achat 4→9 ans)</div></div>
      <div class="stat"><span class="badge">${verdictBadge(r.rentable)}</span><div class="label">verdict</div></div>
    </div>
    <p class="sub">Occupation : ${r.occupancy_source}. Prix : ${r.price_source}.
       Comparables dans le rayon : ${r.n_local_segment} (${r.segment.toLowerCase()}) / ${r.n_local_total} (tous segments).</p>
    ${noteHtml}
    <p class="${dimClass}">${r.message}</p>
    <div class="${dimClass}"><canvas id="chart-sim-table" height="200"></canvas></div>
  `;
  const t = r.table;
  upsertChart("chart-sim-table", {
    type: "bar",
    data: {
      labels: t.map((d) => d.age),
      datasets: [{ data: t.map((d) => d.net_annuel),
                  backgroundColor: t.map((d) => (d.net_annuel >= 0 ? cssVar("--good") : cssVar("--critical"))),
                  borderRadius: 3 }],
    },
    options: {
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { display: false }, ticks: { color: cssVar("--text-muted") },
             title: { display: true, text: "âge du véhicule (ans)", color: cssVar("--text-muted") } },
        y: { grid: { color: cssVar("--grid") }, ticks: { color: cssVar("--text-muted") },
             title: { display: true, text: "net annuel (€, avant frais fixes)", color: cssVar("--text-muted") } },
      },
    },
  });
}

function renderBestResult(r) {
  const segLines = Object.entries(r.segment_stats)
    .map(([seg, s]) => `<li>${seg} : ${(s.occupancy * 100).toFixed(1)}%${ciSuffix(s.occupancy_ci ? { ...s.occupancy_ci, n: s.n_local } : null)} d'occupation, ${s.daily_rate} €/jour</li>`)
    .join("");
  const excludedNote = r.excluded_segments.length
    ? `<p class="empty-note">Segments écartés du classement (trop peu de comparables locaux pour être fiables) : ${
        r.excluded_segments.map((e) => `${e.segment} (n=${e.n_local})`).join(", ")}</p>`
    : "";
  const reliabilityNote = r.verdict_reliable
    ? ""
    : `<p class="sub">Verdicts indéterminés (zone plafonnée, cf. avertissement ci-dessus) — classement à titre indicatif seulement.</p>`;
  const dimClass = r.verdict_reliable ? "" : "dimmed";

  if (!r.models.length) {
    document.getElementById("sim-result").innerHTML = `
      ${capWarningHtml(r)}
      <p class="sub">Demande locale mesurée :</p><ul class="sub">${segLines || "<li>aucune</li>"}</ul>
      ${excludedNote}
      <p class="empty-note">Pas assez de comparables locaux pour classer des modèles à cet endroit — élargis le rayon.</p>`;
    return;
  }

  const rows = r.models.map((m, i) => {
    const profit = m.profit_total != null ? `${Math.round(m.profit_total)} €` : "—";
    return `<tr>
      <td class="num">${i + 1}</td>
      <td>${m.model}</td>
      <td>${m.segment}</td>
      <td class="num">${(m.occupancy * 100).toFixed(1)}%</td>
      <td class="num">${m.daily_rate} €</td>
      <td><span class="dot ${verdictDotClass(m.rentable)}"></span></td>
      <td class="num">${profit}</td>
      <td class="msg">${m.message}</td>
    </tr>`;
  }).join("");

  document.getElementById("sim-result").innerHTML = `
    ${capWarningHtml(r)}
    <p class="sub">Demande locale mesurée (segments avec assez de comparables) :</p>
    <ul class="sub">${segLines}</ul>
    ${excludedNote}
    ${reliabilityNote}
    <div class="${dimClass}" style="overflow-x:auto;">
      <table class="sim-table">
        <thead><tr><th>#</th><th>Modèle</th><th>Segment</th><th>Occ. locale</th><th>Prix utilisé</th><th></th><th>Profit total</th><th>Fenêtre achat/revente</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

document.getElementById("sim-best-btn").addEventListener("click", async () => {
  const btn = document.getElementById("sim-best-btn");
  btn.disabled = true;
  btn.textContent = "Recherche…";
  try {
    const radius = parseFloat(document.getElementById("sim-radius").value) || 1.2;
    const params = new URLSearchParams({ lat: simLatLon.lat, lon: simLatLon.lon, radius_km: radius });
    const res = await fetch(`/api/simulate/best?${params}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    renderBestResult(await res.json());
  } catch (e) {
    document.getElementById("sim-result").innerHTML = `<p class="empty-note">Erreur : ${e.message || e}</p>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Trouver le meilleur véhicule ici";
  }
});

document.getElementById("sim-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const btn = document.getElementById("sim-submit");
  btn.disabled = true;
  btn.textContent = "Calcul…";
  try {
    const payload = {
      make: document.getElementById("sim-make").value,
      model: document.getElementById("sim-model").value,
      propulsion: document.getElementById("sim-propulsion").value,
      lat: simLatLon.lat,
      lon: simLatLon.lon,
      radius_km: parseFloat(document.getElementById("sim-radius").value) || 1.2,
    };
    const priceVal = document.getElementById("sim-price").value;
    if (priceVal) payload.daily_rate = parseFloat(priceVal);

    const res = await fetch("/api/simulate", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    renderSimResult(await res.json());
  } catch (e) {
    document.getElementById("sim-result").innerHTML =
      `<p class="empty-note">Erreur : ${e.message || e}</p>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Estimer la rentabilité";
  }
});

restoreRunState();
loadAllData();
initSimMap();
