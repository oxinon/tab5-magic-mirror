// JS für die Start-/Live-Spiegel-Seite ("/", siehe web_server.py::
// _start_page_html()/START_MIRROR_BODY). Ausgelagert aus einem
// Python-String (Optimierungs-Backlog Punkt 5/7, siehe HANDOFF.md).
// Erwartet window.PAGE_DATA.snapshotTypes (siehe kleines Inline-Script
// direkt vor dieser Datei im HTML - der einzige pro Seitenaufruf
// wirklich dynamische Wert hier).

function fmtMetric(v, digits, unit) {
  return v == null ? '-' : v.toFixed(digits) + (unit || '');
}

function applyClimate(tile, reading) {
  if (!reading || !reading.ok) { tile.textContent = 'nicht verfügbar'; return; }
  tile.textContent = fmtMetric(reading.temp_c, 1, '°C') + ' · ' +
    fmtMetric(reading.humidity, 0, '%') + ' · IAQ ' + fmtMetric(reading.iaq_score, 0, '');
}

function listHtml(items) {
  if (!items || !items.length) return '<span class="hint">keine Einträge</span>';
  return '<ul>' + items.map(i => '<li>' + escHtml(i) + '</li>').join('') + '</ul>';
}

// Rendert eine Kachel anhand ihres Snapshot-Daten-Dicts (siehe
// SNAPSHOT_TYPES in web_server.py / widget_catalog.py::get_snapshot()) -
// dieselben Feldnamen wie die jeweilige widget_sources.py::fetch_X()-
// Funktion zurückgibt, ein Fall pro Typ.
function applySnapshot(valueEl, type, data) {
  if (!data) { valueEl.innerHTML = '<span class="hint">noch keine Daten</span>'; return; }
  if (data.ok === false) { valueEl.textContent = data.msg || 'nicht verfügbar'; return; }
  if (type === 'weather') {
    valueEl.textContent = fmtMetric(data.temperature, 1, '°' + (data.unit || 'C')) +
      ' · ' + (data.description || '');
  } else if (type === 'news') {
    valueEl.innerHTML = listHtml((data.items || []).slice(0, 4));
  } else if (type === 'crypto') {
    valueEl.innerHTML = listHtml((data.prices || []).map(p =>
      p.ticker + ': ' + fmtMetric(p.price, 2, ' ' + (p.currency || '').toUpperCase())));
  } else if (type === 'stocks') {
    valueEl.innerHTML = listHtml((data.prices || []).map(p =>
      p.ticker + ': ' + fmtMetric(p.price, 2, p.currency || '')));
  } else if (type === 'quote') {
    valueEl.textContent = data.text ? ('"' + data.text + '" – ' + (data.author || '')) : 'n/v';
  } else if (type === 'calendar') {
    valueEl.innerHTML = listHtml((data.events || []).slice(0, 4).map(e => e.title));
  } else if (type === 'warnings') {
    const list = data.warnings || [];
    valueEl.innerHTML = list.length ? listHtml(list.map(w => w.title)) : 'keine aktuellen Warnungen';
  } else if (type === 'air_quality_mirror') {
    valueEl.textContent = 'AQI ' + fmtMetric(data.aqi, 0, '') + ' · PM2.5 ' + fmtMetric(data.pm25, 0, '');
  } else if (type === 'elbe_pegel') {
    valueEl.textContent = fmtMetric(data.value_cm, 0, ' cm') + ' (' + (data.station_name || '') + ')';
  } else if (type === 'ews') {
    valueEl.textContent = 'Level ' + fmtMetric(data.emergency_level, 0, '') + ' · ' +
      fmtMetric(data.concurrent_count, 0, ' gleichzeitig');
  } else if (type === 'defcon') {
    valueEl.innerHTML = listHtml((data.regions || []).map(r => r.name + ': ' + r.value));
  } else if (type === 'server_status') {
    // Format bestätigt aus widget_catalog.py::_fetch_server_status():
    // {"ok", "running", "total", "containers": [{"name","status"}]}
    const containers = data.containers || [];
    let html = (data.running != null ? (data.running + '/' + data.total + ' Container laufen') : 'Status OK');
    if (containers.length) {
      html += listHtml(containers.map(c => c.name + ': ' + (c.status === 'running' ? 'läuft' : c.status)));
    }
    valueEl.innerHTML = html;
  } else if (type === 'pc_status') {
    valueEl.textContent = fmtMetric(data.cpu_usage, 0, '% CPU') + ' · ' +
      fmtMetric(data.cpu_temp, 0, '°C') + (data.gpu_usage != null ? (' · ' + fmtMetric(data.gpu_usage, 0, '% GPU')) : '');
  } else {
    valueEl.textContent = 'n/v';
  }
}

function refreshMirror() {
  const tiles = document.querySelectorAll('.mirror-tile');
  return Promise.all([
    fetch('/api/status').then(r => r.json()).catch(() => ({})),
    fetch('/api/ha-states').then(r => r.json()).catch(() => ({})),
    fetch('/api/external-sensors-status').then(r => r.json()).catch(() => []),
    fetch('/api/climate-ext-status').then(r => r.json()).catch(() => ({ok: false})),
    fetch('/api/widget-snapshot').then(r => r.json()).catch(() => ({})),
  ]).then(([status, haStates, extList, climateExt, snapshot]) => {
    const air = status.air || {};
    const primaryAir = (air.local && air.local.ok) ? air.local : air.remote;
    tiles.forEach(tile => {
      const type = tile.dataset.type;
      const valueEl = tile.querySelector('.mirror-value');
      if (!valueEl) return;
      if (type === 'climate') {
        applyClimate(valueEl, primaryAir);
      } else if (type === 'air_quality') {
        valueEl.textContent = (primaryAir && primaryAir.ok && primaryAir.iaq_score != null) ?
          ('IAQ-Score ' + primaryAir.iaq_score.toFixed(0)) : 'nicht verfügbar';
      } else if (type === 'acoustic') {
        valueEl.textContent = (status.mic && status.mic.ok && status.mic.db != null) ?
          (status.mic.db.toFixed(0) + ' dB') : 'nicht verfügbar';
      } else if (type === 'acceleration') {
        valueEl.textContent = (status.accel && status.accel.ok && status.accel.magnitude != null) ?
          (status.accel.magnitude.toFixed(2) + ' g') : 'nicht verfügbar';
      } else if (type === 'climate_ext') {
        applyClimate(valueEl, climateExt);
      } else if (type === 'env_sensor_mirror') {
        const quakeTxt = (status.accel && status.accel.ok) ?
          (status.accel.quake && status.accel.quake.triggered ? ' · Erschütterung!' : ' · ruhig') : '';
        applyClimate(valueEl, primaryAir);
        if (valueEl.textContent !== 'nicht verfügbar') valueEl.textContent += quakeTxt;
      } else if (type === 'ha_entity' || type === 'ha_switch') {
        // ha_switch ist seit Phase C (siehe HANDOFF.md) nur noch Home
        // Assistant - Atom-Relais laufen über "relay_pair" (siehe unten).
        const state = haStates[tile.dataset.entity];
        valueEl.textContent = (state != null && state !== '') ? state : 'n/v';
      } else if (PAGE_DATA.snapshotTypes.indexOf(type) !== -1) {
        applySnapshot(valueEl, type, snapshot[tile.dataset.id]);
      }
      // clock/compliments/todo/logo/relay_pair: server-seitig gerendert
      // bzw. clientseitig per updateClocks()/updateRelayPairs() unten -
      // hier nichts zu tun.
      // Externe Quellen (Phase A) sind namentlich nicht 1:1 einer Kachel
      // zugeordnet (eigenständig konfiguriert, unabhängig vom
      // climate_ext-Widget) - deshalb als eigene Zeile unterhalb des
      // Rasters, siehe extRow unten.
    });
    let extHtml = '';
    extList.forEach(ext => {
      extHtml += '<div class="mirror-tile" style="margin-top:10px;"><div class="mirror-title">' +
        escHtml(ext.name || 'Extern') + '</div><div class="mirror-value">' +
        (ext.ok ? (fmtMetric(ext.temp_c, 1, '°C') + ' · ' + fmtMetric(ext.humidity, 0, '%') +
          ' · IAQ ' + fmtMetric(ext.iaq_score, 0, '')) : 'nicht verfügbar') + '</div></div>';
    });
    const grid = document.querySelector('.mirror-grid');
    let extContainer = document.getElementById('mirror-external-sources');
    if (!extContainer) {
      extContainer = document.createElement('div');
      extContainer.id = 'mirror-external-sources';
      grid.after(extContainer);
    }
    extContainer.innerHTML = extHtml;
  }).catch(() => {});
}

// Server hat keine Uhrzeit fürs "clock"-Widget geliefert (siehe
// web_server.py-Modul-Docstring) - Browser-Uhrzeit ist praktisch
// gleichwertig und braucht keinen Abruf.
function updateClocks() {
  const now = new Date().toTimeString().slice(0, 5);
  document.querySelectorAll('.mirror-clock').forEach(el => { el.textContent = now; });
}

// Atom-Relais-Paare (Phase C, siehe HANDOFF.md) - getrennt von
// refreshMirror(), da jede "relay_pair"-Kachel ihr EIGENES Board (per
// data-board-Index) hat, statt eines gemeinsamen Endpunkts wie bei den
// übrigen Kachel-Typen. Ein Board, das gerade nicht erreichbar ist, soll
// die übrigen Kacheln nicht verzögern - deshalb pro Board ein eigener,
// unabhängiger fetch() statt eines gemeinsamen Promise.all().
function updateRelayPairs() {
  const requests = [];
  document.querySelectorAll('.mirror-tile[data-type="relay_pair"]').forEach(tile => {
    const board = tile.dataset.board;
    requests.push(fetch('/api/atom-status/' + board).then(r => r.json()).then(status => {
      [1, 2].forEach(relay => {
        const el = tile.querySelector('.mirror-value[data-relay="' + relay + '"]');
        if (!el) return;
        const label = el.textContent.split(':')[0];
        if (!status.ok) { el.textContent = label + ': nicht verfügbar'; return; }
        const isOn = !!status['relay' + relay + '_state'];
        el.textContent = label + ': ' + (isOn ? 'AN' : 'AUS');
      });
    }).catch(() => {
      [1, 2].forEach(relay => {
        const el = tile.querySelector('.mirror-value[data-relay="' + relay + '"]');
        if (el) el.textContent = el.textContent.split(':')[0] + ': nicht erreichbar';
      });
    }));
  });
  return Promise.all(requests);
}

updateClocks();
setInterval(updateClocks, 15000);
refreshMirror();
updateRelayPairs();
pollEvery(refreshMirror, 10000);
pollEvery(updateRelayPairs, 10000);
