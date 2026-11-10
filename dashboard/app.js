/* Dashboard client: polls the FastAPI endpoints and drives Chart.js. */

const API = "/api/v1";
const REFRESH_MS = 15000;
const DIAGNOSTIC_WINDOW_MINUTES = 1440; // keep incidents visible across page loads

const PALETTE = [
  "#38bdf8",
  "#a78bfa",
  "#f472b6",
  "#fbbf24",
  "#34d399",
  "#fb7185",
  "#94a3b8",
  "#60a5fa",
];

const state = { minutes: 60, charts: {}, timer: null };

const $ = (id) => document.getElementById(id);

const escapeHtml = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char],
  );

function fmtNumber(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return Number(value).toFixed(digits);
}

function fmtPercent(value, digits = 2) {
  const text = fmtNumber(value, digits);
  return text === "—" ? text : `${text}%`;
}

function fmtTime(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function setText(id, text) {
  const element = $(id);
  if (element) element.textContent = text;
}

async function getJSON(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path} -> HTTP ${response.status}`);
  return response.json();
}

function initCharts() {
  if (typeof Chart === "undefined") {
    console.warn("Chart.js failed to load; charts will stay empty.");
    return;
  }

  Chart.defaults.color = "#8fa0c0";
  Chart.defaults.font.family = "'Segoe UI', Inter, system-ui, sans-serif";
  Chart.defaults.font.size = 11;

  const grid = { color: "rgba(125,155,220,0.10)" };

  state.charts.latency = new Chart($("latencyChart"), {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "WAN latency",
          data: [],
          borderColor: "#38bdf8",
          backgroundColor: "rgba(56,189,248,0.12)",
          fill: true,
          tension: 0.35,
          pointRadius: 0,
          borderWidth: 2,
        },
        {
          label: "API latency",
          data: [],
          borderColor: "#a78bfa",
          backgroundColor: "rgba(167,139,250,0.08)",
          fill: true,
          tension: 0.35,
          pointRadius: 0,
          borderWidth: 2,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          position: "bottom",
          labels: { boxWidth: 8, usePointStyle: true, pointStyle: "circle", padding: 14 },
        },
      },
      scales: {
        x: { grid: { display: false }, ticks: { maxTicksLimit: 8 } },
        y: {
          beginAtZero: true,
          grid,
          title: { display: true, text: "ms" },
        },
      },
    },
  });

  state.charts.rootCause = new Chart($("rootCauseChart"), {
    type: "doughnut",
    data: {
      labels: [],
      datasets: [
        {
          data: [],
          backgroundColor: PALETTE,
          borderColor: "#0d1424",
          borderWidth: 3,
          hoverOffset: 6,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "62%",
      plugins: {
        legend: {
          position: "bottom",
          labels: { boxWidth: 8, usePointStyle: true, pointStyle: "circle", padding: 10 },
        },
      },
    },
  });
}

function renderOverview(overview, incidents) {
  const sampleCount = Number(overview.sample_count || 0);
  const score = Number(overview.network_health_score || 0);

  $("healthScore").textContent = sampleCount ? score.toFixed(1) : "—";
  $("healthScore").style.color = !sampleCount
    ? "var(--muted)"
    : score >= 90
      ? "var(--good)"
      : score >= 75
        ? "var(--warn)"
        : "var(--bad)";
  setText("healthSub", sampleCount ? `${sampleCount.toLocaleString()} samples` : "no data in range");

  const online = Number(overview.online_devices || 0);
  const total = Number(overview.total_devices || 0);
  setText("onlineDevices", total ? `${online} / ${total}` : "—");
  setText("onlineSub", total && online < total ? `${total - online} not online` : "of fleet");

  setText("avgLatency", sampleCount ? `${fmtNumber(overview.avg_latency_ms)} ms` : "—");
  setText("avgLoss", fmtPercent(overview.avg_packet_loss_pct));
  setText("apiErrorRate", fmtPercent(overview.api_error_rate_pct));

  const active = incidents.filter((item) => item.severity !== "info").length || incidents.length;
  setText("incidentCount", String(active));
  setText("incidentSub", `${Number(overview.incident_count || 0)} stored diagnoses`);
}

function renderSeries(series) {
  const chart = state.charts.latency;
  if (!chart) return;

  chart.data.labels = series.map((row) => fmtTime(row.bucket));
  chart.data.datasets[0].data = series.map((row) => row.avg_latency_ms);
  chart.data.datasets[1].data = series.map((row) => row.avg_api_latency_ms);
  chart.update("none");
}

function renderRootCauses(rootCauses) {
  const chart = state.charts.rootCause;
  if (!chart) return;

  if (!rootCauses.length) {
    chart.data.labels = ["No incidents"];
    chart.data.datasets[0].data = [1];
    chart.data.datasets[0].backgroundColor = ["rgba(143,160,192,0.22)"];
  } else {
    chart.data.labels = rootCauses.map((row) => row.root_cause);
    chart.data.datasets[0].data = rootCauses.map((row) => row.incidents);
    chart.data.datasets[0].backgroundColor = rootCauses.map(
      (_, index) => PALETTE[index % PALETTE.length],
    );
  }
  chart.update("none");
}

function renderDevices(devices) {
  const tbody = $("deviceRows");
  if (!tbody) return;

  if (!devices.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty">No gateways reporting.</td></tr>';
    setText("fleetHint", "no devices");
    return;
  }

  tbody.innerHTML = devices
    .map(
      (device) => `
      <tr>
        <td class="device-id">${escapeHtml(device.device_id)}</td>
        <td><span class="pill ${escapeHtml(device.status)}">${escapeHtml(device.status)}</span></td>
        <td>${escapeHtml(device.wan_status ?? "—")}</td>
        <td>${device.latency_ms === null ? "—" : fmtNumber(device.latency_ms)}</td>
        <td>${fmtPercent(device.packet_loss_pct)}</td>
        <td>${device.cpu_pct === null ? "—" : `${fmtNumber(device.cpu_pct, 0)}%`}</td>
        <td>${device.memory_pct === null ? "—" : `${fmtNumber(device.memory_pct, 0)}%`}</td>
        <td>${Number(device.error_count || 0)}</td>
      </tr>`,
    )
    .join("");

  const healthy = devices.filter((device) => device.status === "healthy").length;
  setText("fleetHint", `${healthy} healthy / ${devices.length} total`);
}

function renderIncidents(incidents) {
  const list = $("incidentList");
  if (!list) return;

  if (!incidents.length) {
    list.innerHTML = '<p class="empty">No incidents in the last 24 hours.</p>';
    setText("incidentHint", "all clear");
    return;
  }

  list.innerHTML = incidents
    .map(
      (incident) => `
      <article class="incident ${escapeHtml(incident.severity)}">
        <div class="incident-top">
          <span class="incident-cause">${escapeHtml(incident.root_cause)}</span>
          <span class="hint">${Math.round(Number(incident.confidence || 0) * 100)}%</span>
        </div>
        <p class="incident-meta">
          <span class="device">${escapeHtml(incident.device_id)}</span>
          · ${escapeHtml(incident.category)} · ${fmtTime(incident.created_at)}
        </p>
        <p class="incident-action">${escapeHtml(incident.recommended_action)}</p>
      </article>`,
    )
    .join("");

  setText("incidentHint", `${incidents.length} latest`);
}

async function refresh() {
  const minutes = state.minutes;

  try {
    const [health, overview, series, rootCauses, devices, incidents] = await Promise.all([
      getJSON("/health"),
      getJSON(`${API}/metrics/overview?window_minutes=${minutes}`),
      getJSON(`${API}/metrics/timeseries?window_minutes=${minutes}`),
      getJSON(`${API}/metrics/root-causes?window_minutes=${DIAGNOSTIC_WINDOW_MINUTES}`),
      getJSON(`${API}/devices?window_minutes=${minutes}`),
      getJSON(`${API}/diagnostics?window_minutes=${DIAGNOSTIC_WINDOW_MINUTES}&limit=12`),
    ]);

    renderOverview(overview, incidents);
    renderSeries(series);
    renderRootCauses(rootCauses);
    renderDevices(devices);
    renderIncidents(incidents);

    setText("version", health.version ? `v${health.version}` : "v0.1.0");
    $("liveBadge").classList.remove("stale");
    setText(
      "updated",
      `updated ${new Date().toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })}`,
    );
  } catch (error) {
    console.error(error);
    $("liveBadge").classList.add("stale");
    setText("updated", "refresh failed");
  }
}

function wireRangeButtons() {
  document.querySelectorAll("#rangeGroup button").forEach((button) => {
    button.addEventListener("click", () => {
      document
        .querySelectorAll("#rangeGroup button")
        .forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      state.minutes = Number(button.dataset.minutes);
      refresh();
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  initCharts();
  wireRangeButtons();
  refresh();
  state.timer = setInterval(refresh, REFRESH_MS);
});
