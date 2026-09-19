// JS für den Dashboard-Editor ("/dashboard", siehe web_server.py::
// _dashboard_page_html()/DASHBOARD_BODY). Ausgelagert aus einem
// Python-String (Optimierungs-Backlog Punkt 5/7, siehe HANDOFF.md).
// Erwartet window.PAGE_DATA.cols/rows (siehe kleines Inline-Script direkt
// vor dieser Datei im HTML - die einzigen pro Seitenaufruf wirklich
// dynamischen Werte hier, für addHaRow()'s Klick-Raster).

function loadLogoList(block) {
  const listEl = block.querySelector('.f-logo-list');
  if (!listEl) return;
  const fileFieldEl = block.querySelector('.f-file');
  fetch('/api/list-logos').then(r => r.json()).then(logos => {
    if (!logos.length) {
      listEl.innerHTML = 'Noch keine Logos hochgeladen.';
      return;
    }
    // Bewusst per DOM-API statt innerHTML/Inline-onclick: der Dateiname
    // stammt vom Upload und darf nie als HTML/JS interpretiert werden.
    listEl.textContent = '';
    logos.forEach(logo => {
      const sizeKb = logo.size != null ? (logo.size / 1024).toFixed(0) + ' KB' : '';
      const row = document.createElement('div');
      row.style.cssText = 'display:flex;align-items:center;gap:8px;margin-top:4px;';
      const label = document.createElement('span');
      label.style.flex = '1';
      label.textContent = logo.name + (sizeKb ? ' (' + sizeKb + ')' : '');
      const makeBtn = (text, handler) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'secondary';
        b.style.cssText = 'margin:0;padding:4px 10px;font-size:0.78rem;';
        b.textContent = text;
        b.addEventListener('click', () => handler(b));
        return b;
      };
      row.appendChild(label);
      row.appendChild(makeBtn('verwenden', b => useExistingLogo(b, logo.name, logo.path)));
      row.appendChild(makeBtn('löschen', b => deleteLogo(b, logo.name)));
      listEl.appendChild(row);
    });
  }).catch(() => {
    listEl.textContent = 'Liste konnte nicht geladen werden.';
  });
}

function useExistingLogo(buttonEl, name, path) {
  const block = buttonEl.closest('.widget-block');
  block.querySelector('.f-file').value = path;
}

function deleteLogo(buttonEl, name) {
  if (!confirm('Logo "' + name + '" wirklich löschen?')) return;
  fetch('/api/delete-logo', {
    method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: 'filename=' + encodeURIComponent(name),
  }).then(r => r.json()).then(res => {
    const block = buttonEl.closest('.widget-block');
    if (res.ok) {
      loadLogoList(block);
    } else {
      alert('Löschen fehlgeschlagen: ' + (res.msg || 'unbekannt'));
    }
  }).catch(() => {
    alert('Gerät nicht erreichbar.');
  });
}

function uploadLogo(inputEl) {
  const file = inputEl.files[0];
  if (!file) return;
  const block = inputEl.closest('.widget-block');
  const statusEl = block.querySelector('.f-logo-upload-status');
  const fileFieldEl = block.querySelector('.f-file');
  statusEl.textContent = 'Wird hochgeladen und umgewandelt...';

  const formData = new FormData();
  formData.append('file', file);
  // Bisherigen Dateinamen (ohne .bin) weiterverwenden, falls schon einer
  // gesetzt ist - sonst vom PNG-Dateinamen ableiten.
  // NUR den Dateinamen selbst weiterverwenden, keinen ggf. schon
  // enthaltenen Pfad (z.B. "/flash/static/logo1.bin" nach einem
  // vorherigen erfolgreichen Upload - siehe uploadLogo()-Erfolgsfall
  // unten, der fileFieldEl.value auf den VOLLEN Pfad setzt) - sonst
  // würde der komplette Pfad inkl. Schrägstrichen als "Dateiname" an
  // den Server geschickt und dort zurecht als ungültig abgelehnt
  // (Schutz gegen Pfad-Ausbruch, siehe web_server.py-Route).
  const existingName = (fileFieldEl.value || '').split('/').pop().replace(/\.bin$/, '');
  formData.append('filename', existingName || file.name.replace(/\.[^.]+$/, ''));

  // Kein Content-Type-Header von Hand setzen - der Browser setzt bei
  // FormData automatisch "multipart/form-data; boundary=..." korrekt,
  // von Hand gesetzt würde die Boundary fehlen und der Server könnte
  // nichts parsen.
  fetch('/api/upload-logo', {method: 'POST', body: formData})
    .then(r => r.json())
    .then(res => {
      if (res.ok) {
        fileFieldEl.value = res.path;
        statusEl.textContent = 'Hochgeladen: ' + res.path + ' - "Alles speichern" nicht vergessen.';
        loadLogoList(block);
      } else {
        statusEl.textContent = 'Fehler: ' + (res.msg || 'unbekannt');
      }
    })
    .catch(() => {
      statusEl.textContent = 'Gerät nicht erreichbar.';
    });
}

document.querySelectorAll('.f-logo-list').forEach(el => loadLogoList(el.closest('.widget-block')));

function recomputeSpanOptions(row) {
  const group = row.querySelector('.span-radio-group');
  if (!group) return; // Widget-Typ ohne Breiten-Auswahl (fester 1x)
  const cols = parseInt(row.dataset.cols, 10);
  const typeMax = parseInt(group.dataset.typeMax, 10);
  const posMatch = /-c(\d+)$/.exec(row.querySelector('.position-select').value);
  const col = posMatch ? parseInt(posMatch[1], 10) : 1;
  const fitMax = cols - col + 1;
  const maxSpan = Math.max(1, Math.min(typeMax, fitMax));

  const currentChecked = group.querySelector('input:checked');
  const current = Math.min(currentChecked ? parseInt(currentChecked.value, 10) : 1, maxSpan);
  const name = (group.querySelector('input') || {}).name || ('span_' + Date.now());
  group.innerHTML = '';
  for (let s = 1; s <= maxSpan; s++) {
    const label = document.createElement('label');
    label.className = 'span-option';
    const input = document.createElement('input');
    input.type = 'radio';
    input.className = 'span-radio';
    input.name = name;
    input.value = s;
    if (s === current) input.checked = true;
    label.appendChild(input);
    label.appendChild(document.createTextNode(s + 'x'));
    group.appendChild(label);
  }
}

function recomputeRowSpanOptions(row) {
  // Exakt dieselbe Logik wie recomputeSpanOptions() oben, nur Zeilen
  // statt Spalten (Nutzerwunsch: Notiz-Widget soll auch nach UNTEN
  // mehrere Kacheln nutzen dürfen) - eigene Funktion statt Parametrisierung,
  // da die Positions-Regex (Zeile vs. Spalte) und die CSS-Klassen
  // ("row-span-radio-group" vs. "span-radio-group") sich unterscheiden.
  const group = row.querySelector('.row-span-radio-group');
  if (!group) return; // Widget-Typ ohne Höhen-Auswahl (fester 1x)
  const rows = parseInt(row.dataset.rows, 10);
  const typeMax = parseInt(group.dataset.typeMax, 10);
  const posMatch = /^r(\d+)-/.exec(row.querySelector('.position-select').value);
  const posRow = posMatch ? parseInt(posMatch[1], 10) : 1;
  const fitMax = rows - posRow + 1;
  const maxSpan = Math.max(1, Math.min(typeMax, fitMax));

  const currentChecked = group.querySelector('input:checked');
  const current = Math.min(currentChecked ? parseInt(currentChecked.value, 10) : 1, maxSpan);
  const name = (group.querySelector('input') || {}).name || ('rowspan_' + Date.now());
  group.innerHTML = '';
  for (let s = 1; s <= maxSpan; s++) {
    const label = document.createElement('label');
    label.className = 'span-option';
    const input = document.createElement('input');
    input.type = 'radio';
    input.className = 'row-span-radio';
    input.name = name;
    input.value = s;
    if (s === current) input.checked = true;
    label.appendChild(input);
    label.appendChild(document.createTextNode(s + 'x'));
    group.appendChild(label);
  }
}

function buildPositionGridHtml(cols, rows, selected) {
  let cells = '';
  for (let r = 1; r <= rows; r++) {
    for (let c = 1; c <= cols; c++) {
      const pos = 'r' + r + '-c' + c;
      cells += '<button type="button" class="pos-cell' + (pos === selected ? ' selected' : '') +
        '" data-pos="' + pos + '"></button>';
    }
  }
  return '<input type="hidden" class="position-select" value="' + selected + '">' +
    '<div class="pos-grid" style="grid-template-columns:repeat(' + cols + ', 20px);" ' +
    'data-cols="' + cols + '" data-rows="' + rows + '">' + cells + '</div>';
}

function wirePositionGrid(row) {
  const grid = row.querySelector('.pos-grid');
  const hiddenInput = row.querySelector('.position-select');
  if (!grid || !hiddenInput) return;
  grid.querySelectorAll('.pos-cell').forEach(cell => {
    cell.addEventListener('click', () => {
      grid.querySelectorAll('.pos-cell').forEach(c => c.classList.remove('selected'));
      cell.classList.add('selected');
      hiddenInput.value = cell.dataset.pos;
      hiddenInput.dispatchEvent(new Event('change'));
    });
  });
}

function addHaRow() {
  const block = document.createElement('div');
  block.className = 'widget-block';
  block.innerHTML =
    '<div class="layout-row" data-cols="' + PAGE_DATA.cols + '" data-rows="' + PAGE_DATA.rows + '" data-ha="true">' +
    '<input type="text" class="title-input" placeholder="Titel">' +
    '<input type="text" class="entity-input" placeholder="entity_id">' +
    '<select class="type-select"><option value="ha_entity">Anzeige (Sensor)</option>' +
    '<option value="ha_switch">Schalter (Home Assistant)</option></select>' +
    '<div class="widget-controls-col">' +
    '<label class="layout-checkbox"><input type="checkbox" class="enabled-cb" checked>an</label>' +
    '<span class="span-fixed">1x</span>' +
    '</div>' +
    '<div class="pos-grid-wrap">' + buildPositionGridHtml(PAGE_DATA.cols, PAGE_DATA.rows, 'r1-c1') + '</div>' +
    '<button type="button" class="remove secondary" onclick="this.closest(\'.widget-block\').remove()">&times;</button>' +
    '</div>';
  // In die "Home Assistant"-Kategorie einhängen, falls es schon eine
  // gibt (an ihrem Titel erkannt) - sonst direkt in den Container
  // (passiert nur, wenn noch gar keine HA-Kachel existiert und die
  // Kategorie deshalb noch gar nicht gerendert wurde).
  let targetBody = null;
  document.querySelectorAll('.widget-category summary').forEach(summary => {
    if (summary.textContent.indexOf('Home Assistant') !== -1) {
      targetBody = summary.parentElement.querySelector('.category-body');
    }
  });
  (targetBody || document.getElementById('dashboard-layout-rows')).appendChild(block);
  wirePositionGrid(block.querySelector('.layout-row'));
}

function debounce(fn, ms) {  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

function wireGeoSearch(block) {
  const input = block.querySelector('.f-geosearch');
  if (!input) return;
  const resultsEl = block.querySelector('.f-georesults');
  const selectedEl = block.querySelector('.f-geoselected');
  const latEl = block.querySelector('.f-latitude');
  const lonEl = block.querySelector('.f-longitude');
  const locEl = block.querySelector('.f-location');
  input.addEventListener('input', debounce(async (e) => {
    resultsEl.innerHTML = '';
    const q = e.target.value;
    if (!q || q.length < 2) return;
    const res = await (await fetch('/api/geocode?q=' + encodeURIComponent(q))).json();
    (res.results || []).forEach(r => {
      const div = document.createElement('div');
      div.textContent = r.label;
      div.onclick = () => {
        latEl.value = r.latitude;
        lonEl.value = r.longitude;
        // Nur den Ortsnamen selbst (erster Teil vor dem Komma) als
        // Kachel-Titel merken, nicht "Ort, Region, Land" komplett.
        locEl.value = r.label.split(',')[0].trim();
        selectedEl.textContent = 'Aktuell: ' + r.label + ' (' + r.latitude.toFixed(4) + ', ' + r.longitude.toFixed(4) + ')';
        resultsEl.innerHTML = '';
        input.value = '';
      };
      resultsEl.appendChild(div);
    });
  }, 400));
}

function wireAgsSearch(block) {
  const input = block.querySelector('.f-agssearch');
  if (!input) return;
  const resultsEl = block.querySelector('.f-agsresults');
  const selectedEl = block.querySelector('.f-agsselected');
  const arsEl = block.querySelector('.f-ars');
  input.addEventListener('input', debounce(async (e) => {
    resultsEl.innerHTML = '';
    const q = e.target.value;
    if (!q || q.length < 2) return;
    const res = await (await fetch('/api/ags-search?q=' + encodeURIComponent(q))).json();
    (res.results || []).forEach(r => {
      const div = document.createElement('div');
      div.textContent = r.label;
      div.onclick = () => {
        arsEl.value = r.ars;
        selectedEl.textContent = 'Regionalschlüssel (ARS): ' + r.ars + ' (' + r.label + ')';
        resultsEl.innerHTML = '';
        input.value = '';
      };
      resultsEl.appendChild(div);
    });
  }, 400));
}

function applyWidgetDetails(widget, block) {
  const type = widget.type;
  if (type === 'weather' || type === 'air_quality_mirror') {
    const lat = block.querySelector('.f-latitude').value;
    const lon = block.querySelector('.f-longitude').value;
    if (lat) widget.latitude = parseFloat(lat);
    if (lon) widget.longitude = parseFloat(lon);
    const locEl = block.querySelector('.f-location');
    if (locEl && locEl.value) widget.location = locEl.value;
    const unitsEl = block.querySelector('.f-units');
    if (unitsEl) widget.units = unitsEl.value;
  } else if (type === 'warnings') {
    widget.ars = block.querySelector('.f-ars').value;
  } else if (type === 'news') {
    const sources = [];
    block.querySelectorAll('.f-news-name').forEach((nameEl, i) => {
      const urlEl = block.querySelectorAll('.f-news-url')[i];
      const url = urlEl.value.trim();
      if (url) sources.push({ name: nameEl.value.trim() || ('Quelle ' + (i + 1)), feedUrl: url });
    });
    widget.sources = sources;
    widget.max_items = parseInt(block.querySelector('.f-max-items').value || '5', 10);
  } else if (type === 'crypto') {
    widget.symbols = block.querySelector('.f-symbols').value.split(',').map(s => s.trim()).filter(Boolean);
    widget.currency = block.querySelector('.f-currency').value;
  } else if (type === 'stocks') {
    widget.symbols = block.querySelector('.f-symbols').value.split(',').map(s => s.trim()).filter(Boolean);
  } else if (type === 'defcon') {
    widget.url = block.querySelector('.f-url').value.trim();
    applySecretField(widget, 'api_key', block.querySelector('.f-api-key'));
  } else if (type === 'calendar') {
    applySecretField(widget, 'ical_url', block.querySelector('.f-ical-url'));
    widget.max_events = parseInt(block.querySelector('.f-max-events').value || '5', 10);
    widget.days_ahead = parseInt(block.querySelector('.f-days-ahead').value || '14', 10);
  } else if (type === 'elbe_pegel') {
    widget.station_name = block.querySelector('.f-station-name').value.trim();
  } else if (type === 'climate_ext' || type === 'pc_status') {
    widget.base_url = block.querySelector('.f-base-url').value.trim();
  } else if (type === 'logo') {
    widget.file = block.querySelector('.f-file').value.trim();
  } else if (type === 'compliments') {
    widget.items = block.querySelector('.f-messages').value.split(String.fromCharCode(10)).map(s => s.trim()).filter(Boolean);
  } else if (type === 'clock') {
    widget.format24h = block.querySelector('.f-format24h').checked;
    widget.show_seconds = block.querySelector('.f-show-seconds').checked;
    widget.show_date = block.querySelector('.f-show-date').checked;
  } else if (type === 'acceleration') {
    widget.sta_tau_s = parseFloat(block.querySelector('.f-sta-tau').value);
    widget.lta_tau_s = parseFloat(block.querySelector('.f-lta-tau').value);
    widget.trigger_ratio = parseFloat(block.querySelector('.f-trigger-ratio').value);
    widget.alarm_beep = block.querySelector('.f-alarm-beep').checked;
    widget.alarm_tone1_hz = parseInt(block.querySelector('.f-alarm-tone1').value, 10);
    widget.alarm_tone2_hz = parseInt(block.querySelector('.f-alarm-tone2').value, 10);
    widget.alarm_beep_ms = parseInt(block.querySelector('.f-alarm-beep-ms').value, 10);
    widget.alarm_gap_ms = parseInt(block.querySelector('.f-alarm-gap-ms').value, 10);
    widget.alarm_repeats = parseInt(block.querySelector('.f-alarm-repeats').value, 10);
    widget.alarm_volume_percent = parseInt(block.querySelector('.f-alarm-volume').value, 10);
  } else if (type === 'relay_pair') {
    // Zwei frei beschriftbare Relais-Labels, direkt in der Zeile selbst
    // (nicht in einer aufklappbaren Details-Sektion) - siehe
    // web_server.py::_build_block() relay_pair-Zweig.
    const inputs = Array.from(block.querySelectorAll('.relay-label-input'));
    inputs.sort((a, b) => parseInt(a.dataset.slot, 10) - parseInt(b.dataset.slot, 10));
    widget.labels = inputs.map(el => el.value.trim() || 'Relais');
  }
}

// Geheime Werte (Kalender-URL, API-Key) werden nie an den Browser ausgeliefert.
// Leeres Feld = unveraendert (der Server behaelt den gespeicherten Wert),
// "-" = Wert loeschen, sonst neuen Wert setzen.
function applySecretField(widget, key, inputEl) {
  const v = inputEl.value.trim();
  if (v === '') { delete widget[key]; return; }
  widget[key] = (v === '-') ? '' : v;
}

function saveDashboardLayout() {
  const widgets = [];
  document.querySelectorAll('.widget-block').forEach((block, idx) => {
    const row = block.querySelector('.layout-row');
    const isHa = row.dataset.ha === 'true';
    let widget;
    if (isHa) {
      const existing = block.dataset.widget ? JSON.parse(block.dataset.widget) : null;
      widget = {
        id: existing ? existing.id : ('custom_' + Date.now() + '_' + idx),
        type: row.querySelector('.type-select').value,
        title: row.querySelector('.title-input').value,
        entity_id: row.querySelector('.entity-input').value,
      };
    } else {
      widget = JSON.parse(block.dataset.widget);
      applyWidgetDetails(widget, block);
    }
    widget.position = row.querySelector('.position-select').value;
    const spanGroup = row.querySelector('.span-radio-group');
    if (spanGroup) {
      const checkedSpan = spanGroup.querySelector('input:checked');
      widget.col_span = checkedSpan ? parseInt(checkedSpan.value, 10) : 1;
    }
    const rowSpanGroup = row.querySelector('.row-span-radio-group');
    if (rowSpanGroup) {
      const checkedRowSpan = rowSpanGroup.querySelector('input:checked');
      widget.row_span = checkedRowSpan ? parseInt(checkedRowSpan.value, 10) : 1;
    }
    widget.enabled = row.querySelector('.enabled-cb').checked;
    widgets.push(widget);
  });
  const nameInput = document.getElementById('profile-name-input');
  fetch('/layout/dashboard/save', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      profile_id: PAGE_DATA.profileId,
      name: nameInput ? nameInput.value.trim() : null,
      widgets: widgets,
    }),
  }).then(r => r.json()).then(res => {
    if (res.ok) {
      document.getElementById('layout-status').textContent =
        'Gespeichert um ' + new Date().toLocaleTimeString() + ' - wirkt sofort.';
    } else {
      document.getElementById('layout-status').textContent = 'Fehler: ' + (res.msg || 'unbekannt');
    }
  }).catch(() => {
    document.getElementById('layout-status').textContent = 'Gerät nicht erreichbar.';
  });
}

function setActiveDashboard() {
  fetch('/dashboard/set-active', {
    method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: 'profile_id=' + encodeURIComponent(PAGE_DATA.profileId),
  }).then(r => r.json()).then(res => {
    if (res.ok) {
      document.getElementById('layout-status').textContent =
        'Als aktives Dashboard gesetzt - wirkt sofort.';
      setTimeout(() => location.reload(), 1000);
    } else {
      document.getElementById('layout-status').textContent = 'Fehler: ' + (res.msg || 'unbekannt');
    }
  }).catch(() => {
    document.getElementById('layout-status').textContent = 'Gerät nicht erreichbar.';
  });
}

function rebootDevice() {
  if (!confirm('Tab5 wirklich neu starten? Nicht gespeicherte Änderungen gehen dabei verloren.')) return;
  document.getElementById('layout-status').textContent = 'Neustart angefordert...';
  fetch('/system/reboot', {method: 'POST'}).catch(() => {});
  // Die Verbindung bricht durch den Neustart normalerweise sofort ab -
  // das ist erwartet, kein Fehler. Kurz warten und dann die Seite neu
  // laden, in der Hoffnung, dass der Tab5 bis dahin wieder online ist
  // (im Heimnetz meist ~10-15s, je nach WLAN-Verbindungsdauer).
  setTimeout(() => location.reload(), 15000);
}

document.querySelectorAll('.widget-block').forEach(block => {
  const row = block.querySelector('.layout-row');
  row.querySelector('.position-select').addEventListener('change', () => {
    recomputeSpanOptions(row);
    recomputeRowSpanOptions(row);
  });
  wirePositionGrid(row);
  wireGeoSearch(block);
  wireAgsSearch(block);
});
