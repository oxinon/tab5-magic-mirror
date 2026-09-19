// JS für die Sensor-Dashboard-Seite ("/sensors", siehe web_server.py::
// _sensors_page_html()/SENSORS_BODY). Ausgelagert aus einem Python-String
// (Optimierungs-Backlog Punkt 5/7, siehe HANDOFF.md). Erwartet
// window.PAGE_DATA.externalSources (siehe kleines Inline-Script direkt
// vor dieser Datei im HTML - der einzige pro Seitenaufruf wirklich
// dynamische Wert hier) und dass Chart.js bereits geladen ist (siehe
// "/static/chart.min.js", Optimierungs-Backlog Punkt 1).

// Eine Farbe pro konfigurierter externer Quelle, in der Reihenfolge von
// config.json::room_sensor.external_sources (nur Quellen mit gesetzter
// base_url). Analog zur Farbabstufung im Tab5-eigenen
// sensor_history_screen.py::_blend_white() - je "weiter hinten", desto
// heller/blauer, damit lokal vs. extern 1 vs. extern 2 auf einen Blick
// unterscheidbar bleiben.
const EXTERNAL_SOURCES = PAGE_DATA.externalSources;
const EXTERNAL_COLORS = ['#3d7fff', '#7fa8ff'];

const chartDefaults = {
  responsive: true, maintainAspectRatio: false, animation: false,
  interaction: {intersect: false},
  plugins: {legend: {labels: {color: '#ccc', boxWidth: 12, font: {size: 11}}}},
  scales: {
    x: {ticks: {color: '#888', maxTicksLimit: 6, font: {size: 10}}, grid: {color: '#2a2d35'}},
    y: {ticks: {color: '#888', font: {size: 10}}, grid: {color: '#2a2d35'}}
  }
};

function makeLineChart(elId, label, color) {
  return new Chart(document.getElementById(elId), {
    type: 'line',
    data: {labels: [], datasets: [{label: label, data: [], borderColor: color,
      backgroundColor: 'transparent', tension: 0.2, pointRadius: 0}]},
    options: chartDefaults,
  });
}

function addChartDataset(chart, label, color) {
  chart.data.datasets.push({label: label, data: [], borderColor: color,
    backgroundColor: 'transparent', tension: 0.2, pointRadius: 0});
  return chart.data.datasets.length - 1;
}

const tempChart = makeLineChart('chart-temp', 'Temperatur (°C)', '#c9a15a');
const humidityChart = makeLineChart('chart-humidity', 'Luftfeuchtigkeit (%)', '#5aa9c9');
const iaqChart = makeLineChart('chart-iaq', 'Luftqualitäts-Score', '#7fae7a');
const soundChart = makeLineChart('chart-sound', 'Schallpegel', '#f5b942');
const accelChart = makeLineChart('chart-accel', 'Beschleunigung |a| (g)', '#b96a5a');
const ALL_CHARTS = [tempChart, humidityChart, iaqChart, soundChart, accelChart];

// Pro konfigurierter externer Quelle (0-2, siehe EXTERNAL_SOURCES oben)
// je einen zusätzlichen Datensatz auf den drei betroffenen Grafiken -
// NUR Live-Punkte (kein Eintrag in /api/history, da externe Quellen
// nicht auf der Tab5-SD-Karte mitgeloggt werden), füllt sich also erst,
// während die Seite geöffnet ist, ohne rückwirkende Historie.
const externalChartIdx = EXTERNAL_SOURCES.map((src, i) => ({
  temp: addChartDataset(tempChart, src.name, EXTERNAL_COLORS[i % EXTERNAL_COLORS.length]),
  humidity: addChartDataset(humidityChart, src.name, EXTERNAL_COLORS[i % EXTERNAL_COLORS.length]),
  iaq: addChartDataset(iaqChart, src.name, EXTERNAL_COLORS[i % EXTERNAL_COLORS.length]),
}));

function timeLabel(ts) { return new Date(ts * 1000).toTimeString().slice(0, 5); }

function loadHistory() {
  const hours = document.getElementById('hours-select').value;
  fetch('/api/history?hours=' + hours).then(r => r.json()).then(rows => {
    const labels = rows.map(r => timeLabel(r.timestamp));
    tempChart.data.labels = labels; tempChart.data.datasets[0].data = rows.map(r => r.temp_c);
    humidityChart.data.labels = labels; humidityChart.data.datasets[0].data = rows.map(r => r.humidity);
    iaqChart.data.labels = labels; iaqChart.data.datasets[0].data = rows.map(r => r.iaq_score);
    soundChart.data.labels = labels; soundChart.data.datasets[0].data = rows.map(r => r.sound_db);
    accelChart.data.labels = labels; accelChart.data.datasets[0].data = rows.map(r => r.accel_magnitude);
    ALL_CHARTS.forEach(c => c.update('none'));
  }).catch(() => {});
}

function appendLivePoint(chart, value, datasetIdx) {
  if (value == null) return;
  const idx = datasetIdx || 0;
  if (idx === 0) chart.data.labels.push(timeLabel(Date.now() / 1000));
  chart.data.datasets[idx].data.push(value);
  const maxLen = chart.data.labels.length;
  if (chart.data.datasets[idx].data.length > maxLen) {
    chart.data.datasets[idx].data.shift();
  }
  if (idx === 0 && chart.data.labels.length > 300) {
    chart.data.labels.shift();
    chart.data.datasets.forEach(ds => { if (ds.data.length > 300) ds.data.shift(); });
  }
  chart.update('none');
}

function metricHtml(label, value, unit) {
  return '<div class="metric"><div class="label">' + label + '</div><div class="value">' +
    (value != null ? value : '-') + (unit || '') + '</div></div>';
}

function sourceBlockHtml(title, reading) {
  if (!reading) return '';
  if (!reading.ok) {
    return '<span class="source-label">' + escHtml(title) + '</span><div class="hint">nicht verfügbar' +
      (reading.msg ? (': ' + escHtml(reading.msg)) : '') + '</div>';
  }
  return '<span class="source-label">' + escHtml(title) + '</span><div class="metric-grid">' +
    metricHtml('Temperatur', reading.temp_c != null ? reading.temp_c.toFixed(1) : null, '°C') +
    metricHtml('Feuchte', reading.humidity != null ? reading.humidity.toFixed(0) : null, '%') +
    metricHtml('Luftdruck', reading.pressure_hpa != null ? reading.pressure_hpa.toFixed(0) : null, ' hPa') +
    metricHtml('IAQ-Score', reading.iaq_score != null ? reading.iaq_score.toFixed(0) : null, '') +
    '</div>';
}

function updateStatus() {
  const statusRequest = fetch('/api/status').then(r => r.json()).then(s => {
    let html = '';
    const air = s.air || {};
    if (air.local) html += sourceBlockHtml('Lokal (BME688)', air.local);
    if (air.remote) html += sourceBlockHtml('Remote (T-Display-S3)', air.remote);
    html += '<span class="source-label">Akustik / Beschleunigung</span><div class="metric-grid">' +
      metricHtml('Schallpegel', s.mic && s.mic.ok && s.mic.db != null ? s.mic.db.toFixed(0) : null) +
      metricHtml('Beschleunigung', s.accel && s.accel.ok && s.accel.magnitude != null ? s.accel.magnitude.toFixed(2) : null, 'g') +
      metricHtml('Erschütterung', s.accel && s.accel.ok ? (s.accel.quake.triggered ? 'JA' : 'nein') : null) +
      '</div>';
    document.getElementById('live-local').innerHTML = html;

    const localAir = air.local;
    if (localAir && localAir.ok && localAir.iaq_confidence != null) {
      document.getElementById('iaq-confidence').textContent = localAir.iaq_confidence.toFixed(0) + '%';
    }

    const primaryAir = (air.local && air.local.ok) ? air.local : air.remote;
    if (primaryAir && primaryAir.ok) {
      appendLivePoint(tempChart, primaryAir.temp_c);
      appendLivePoint(humidityChart, primaryAir.humidity);
      appendLivePoint(iaqChart, primaryAir.iaq_score);
    }
    if (s.mic && s.mic.ok) appendLivePoint(soundChart, s.mic.db);
    if (s.accel && s.accel.ok) appendLivePoint(accelChart, s.accel.magnitude);
  }).catch(() => {});

  // Externe Quellen (0-2, siehe EXTERNAL_SOURCES oben) - eigener Abruf,
  // damit ein nicht erreichbares externes Gerät die übrige Anzeige oben
  // nicht blockiert. Liest den zuletzt vom Hintergrund-Task geholten
  // Wert (siehe get_external_sensors_state-Docstring in web_server.py),
  // kein Live-Abruf bei jedem Seiten-Reload.
  if (EXTERNAL_SOURCES.length === 0) return statusRequest;
  const externalRequest = fetch('/api/external-sensors-status').then(r => r.json()).then(list => {
    let html = '';
    list.forEach((reading, i) => {
      html += sourceBlockHtml(reading.name || ('Extern ' + (i + 1)), reading);
      if (reading.ok && externalChartIdx[i]) {
        appendLivePoint(tempChart, reading.temp_c, externalChartIdx[i].temp);
        appendLivePoint(humidityChart, reading.humidity, externalChartIdx[i].humidity);
        appendLivePoint(iaqChart, reading.iaq_score, externalChartIdx[i].iaq);
      }
    });
    document.getElementById('live-external').innerHTML = html;
  }).catch(() => {
    document.getElementById('live-external').innerHTML = '';
  });
  return Promise.all([statusRequest, externalRequest]);
}

function saveSettings() {
  const mode = document.getElementById('mode-select').value;
  const hours = document.getElementById('hours-select').value;
  const iaqMode = document.getElementById('iaq-mode-select').value;
  const sdLogInterval = document.getElementById('sd-log-interval').value;
  fetch('/save', {
    method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: 'mode=' + encodeURIComponent(mode) + '&web_chart_hours=' + encodeURIComponent(hours) +
      '&iaq_baseline_mode=' + encodeURIComponent(iaqMode) +
      '&sd_log_interval_s=' + encodeURIComponent(sdLogInterval),
  }).then(r => r.json()).then(() => {
    document.getElementById('save-status').textContent =
      'Gespeichert um ' + new Date().toLocaleTimeString() + ' - wirkt sofort, kein Neustart nötig.';
    loadHistory();
  }).catch(() => {
    document.getElementById('save-status').textContent = 'Gerät nicht erreichbar.';
  });
}

function recalibrate() {
  fetch('/recalibrate', {method: 'POST'}).then(r => r.json()).then(() => {
    document.getElementById('recalibrate-status').textContent =
      'Neu kalibriert um ' + new Date().toLocaleTimeString() + '.';
  }).catch(() => {
    document.getElementById('recalibrate-status').textContent = 'Gerät nicht erreichbar.';
  });
}

function saveSensorOffsets() {
  const body = 'temp_c=' + encodeURIComponent(document.getElementById('offset-temp').value) +
    '&humidity=' + encodeURIComponent(document.getElementById('offset-humidity').value) +
    '&pressure_hpa=' + encodeURIComponent(document.getElementById('offset-pressure').value);
  fetch('/sensor-offsets/save', {
    method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: body,
  }).then(r => r.json()).then(() => {
    document.getElementById('offsets-save-status').textContent =
      'Gespeichert um ' + new Date().toLocaleTimeString() + ' - wirkt sofort, kein Neustart nötig.';
  }).catch(() => {
    document.getElementById('offsets-save-status').textContent = 'Gerät nicht erreichbar.';
  });
}

loadHistory();
updateStatus();
pollEvery(updateStatus, 5000);
