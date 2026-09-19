// JS für die System-Seite ("/system", siehe web_server.py::
// _system_page_html()/SYSTEM_BODY). Ausgelagert aus einem Python-String
// (Optimierungs-Backlog Punkt 5/7, siehe HANDOFF.md) - keine
// serverseitig eingebetteten dynamischen Werte nötig, alles kommt über
// die üblichen API-Endpunkte.

function updateSystemInfo() {
  return fetch('/api/system-info').then(r => r.json()).then(s => {
    document.getElementById('mem-status').innerHTML = s.mem_free != null ?
      ('<div class="metric-grid"><div class="metric"><div class="label">Freier Speicher</div><div class="value">' +
        (s.mem_free / 1024).toFixed(0) + ' KB</div></div></div>') :
      '<div class="hint">Nicht verfügbar.</div>';

    const batteryEl = document.getElementById('battery-status');
    if (s.percent != null || s.voltage_mv != null) {
      let html = '<div class="metric-grid">';
      if (s.percent != null) {
        html += '<div class="metric"><div class="label">Ladestand</div><div class="value">' + s.percent + '%</div></div>';
      }
      if (s.voltage_mv != null) {
        html += '<div class="metric"><div class="label">Spannung</div><div class="value">' + (s.voltage_mv / 1000).toFixed(1) + ' V</div></div>';
      }
      html += '</div>';
      batteryEl.innerHTML = html;
    } else {
      batteryEl.innerHTML = '<div class="hint">Nicht verfügbar.</div>';
    }
  }).catch(() => {
    document.getElementById('mem-status').innerHTML = '<div class="hint">Gerät nicht erreichbar.</div>';
    document.getElementById('battery-status').innerHTML = '<div class="hint">Gerät nicht erreichbar.</div>';
  });
}

function modeLabel(mode) {
  if (mode === 'sta') return 'Verbunden (Station)';
  if (mode === 'ap') return 'Eigener Access Point';
  return 'Unbekannt';
}

function updateWifiStatus() {
  return fetch('/api/wifi-status').then(r => r.json()).then(s => {
    let html = '<div class="metric-grid">' +
      '<div class="metric"><div class="label">Modus</div><div class="value">' + modeLabel(s.mode) + '</div></div>' +
      '<div class="metric"><div class="label">Netzwerk</div><div class="value">' + escHtml(s.ssid || '-') + '</div></div>' +
      '<div class="metric"><div class="label">IP-Adresse</div><div class="value">' + escHtml(s.ip || '-') + '</div></div>';
    if (s.rssi != null) {
      html += '<div class="metric"><div class="label">Signal</div><div class="value">' + s.rssi + ' dBm</div></div>';
    }
    html += '</div>';
    document.getElementById('wifi-status').innerHTML = html;
  }).catch(() => {
    document.getElementById('wifi-status').innerHTML = '<div class="hint">Gerät nicht erreichbar.</div>';
  });
}

function connectWifi() {
  const ssid = document.getElementById('wifiSsid').value;
  const password = document.getElementById('wifiPassword').value;
  if (!ssid) return;
  document.getElementById('connect-status').textContent =
    'Verbinde... (bis zu 15s) - die Zugangsdaten werden erst nach erfolgreicher Verbindung gespeichert. ' +
    'Klappt es nicht, startet das Gerät wieder den Access Point "Tab5-Setup".';
  fetch('/wifi/connect', {
    method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: 'ssid=' + encodeURIComponent(ssid) + '&password=' + encodeURIComponent(password),
  }).then(r => r.json()).then(() => {
    // Verbindung läuft im Hintergrund weiter - Status pollt automatisch.
    // Achtung: falls die Verbindung klappt, wechselt das Gerät die IP -
    // diese Seite (auf 192.168.4.1) ist dann evtl. nicht mehr erreichbar,
    // das ist normal, kein Fehler.
    setTimeout(updateWifiStatus, 3000);
    setTimeout(updateWifiStatus, 8000);
    setTimeout(updateWifiStatus, 15000);
  }).catch(() => {
    document.getElementById('connect-status').textContent =
      'Anfrage gesendet - falls die Verbindung klappt, wechselt die IP-Adresse und diese Seite lädt nicht mehr neu (normal).';
  });
}

function startAp() {
  if (!confirm('Wirklich in den Access-Point-Modus wechseln? Das Gerät ist danach nur noch über das WLAN "Tab5-Setup" erreichbar.')) return;
  document.getElementById('ap-status').textContent = 'Wechsle in Access-Point-Modus...';
  fetch('/wifi/ap', {method: 'POST'}).then(r => r.json()).then(() => {
    updateWifiStatus();
    document.getElementById('ap-status').textContent = 'Access Point aktiv.';
  }).catch(() => {
    document.getElementById('ap-status').textContent = 'Gerät nicht erreichbar.';
  });
}

function saveSystemSettings() {
  const theme = document.getElementById('theme-select').value;
  const lang = document.getElementById('lang-select').value;
  fetch('/system/save', {
    method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: 'theme_mode=' + encodeURIComponent(theme) + '&language=' + encodeURIComponent(lang),
  }).then(r => r.json()).then(() => {
    document.getElementById('system-save-status').textContent =
      'Gespeichert um ' + new Date().toLocaleTimeString() + ' - wirkt innerhalb ca. 1s ohne Neustart.';
  }).catch(() => {
    document.getElementById('system-save-status').textContent = 'Gerät nicht erreichbar.';
  });
}

function startUiflowMode() {
  const status = document.getElementById('uiflow-status');
  if (!confirm('Tab5 neu starten und das UIFlow2-Startmenü zeigen?\n\nDieses Web-UI ist danach evtl. nicht erreichbar. ' +
               'Zurück: Menü "starten/weiter" wählen oder den Tab5 noch einmal neu starten (Reset oder Strom aus/an).\n' +
               'In UIFlow2 nur RUN benutzen, nicht DOWNLOAD (überschreibt main.py).')) return;
  fetch('/system/uiflow-mode', {method: 'POST'}).then(r => r.json()).then(res => {
    status.textContent = res.ok ? 'Neustart ins UIFlow2-Startmenü ...' : (res.msg || 'Fehler.');
  }).catch(() => { status.textContent = 'Gerät nicht erreichbar.'; });
}

function saveWebPassword() {
  const status = document.getElementById('web-password-status');
  const pw = document.getElementById('web-password').value;
  const pw2 = document.getElementById('web-password2').value;
  if (pw !== pw2) { status.textContent = 'Die Passwörter stimmen nicht überein.'; return; }
  if (pw !== '' && pw.length < 8) { status.textContent = 'Mindestens 8 Zeichen.'; return; }
  if (pw === '' && !confirm('Passwortschutz wirklich ausschalten? Danach hat jeder im Netzwerk Zugriff.')) return;
  fetch('/system/web-password', {
    method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: 'password=' + encodeURIComponent(pw) + '&confirm=' + encodeURIComponent(pw2),
  }).then(r => r.json()).then(res => {
    status.textContent = res.msg || (res.ok ? 'Gespeichert.' : 'Fehler.');
    if (res.ok) {
      document.getElementById('web-password').value = '';
      document.getElementById('web-password2').value = '';
      setTimeout(() => location.reload(), 2500);
    }
  }).catch(() => { status.textContent = 'Gerät nicht erreichbar.'; });
}

function saveServerSettings() {
  const enabled = document.getElementById('server-enabled').checked;
  const baseUrl = document.getElementById('server-base-url').value;
  fetch('/server/save', {
    method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: 'enabled=' + (enabled ? '1' : '0') + '&base_url=' + encodeURIComponent(baseUrl),
  }).then(r => r.json()).then(() => {
    document.getElementById('server-save-status').textContent =
      'Gespeichert - wirkt innerhalb ca. 1s ohne Neustart.';
  }).catch(() => {
    document.getElementById('server-save-status').textContent = 'Gerät nicht erreichbar.';
  });
}

updateWifiStatus();
pollEvery(updateWifiStatus, 8000);
updateSystemInfo();
pollEvery(updateSystemInfo, 15000);
