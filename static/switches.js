// JS für die Schalter-Seite ("/switches", siehe web_server.py::
// _switches_page_html()/SWITCHES_BODY). Ausgelagert aus einem
// Python-String (Optimierungs-Backlog Punkt 5/7, siehe HANDOFF.md).
// Erwartet window.PAGE_DATA.atomBoardCount (siehe kleines Inline-Script
// direkt vor dieser Datei im HTML - der einzige pro Seitenaufruf wirklich
// dynamische Wert hier).

function updateSwitchStatus() {
  const requests = [];
  for (let board = 0; board < PAGE_DATA.atomBoardCount; board++) {
    requests.push(fetch('/api/atom-status/' + board).then(r => r.json()).then(status => {
      document.querySelectorAll('.switch-row[data-board="' + board + '"]').forEach(row => {
        const relay = parseInt(row.dataset.relay, 10);
        const stateEl = row.querySelector('.state');
        const checkbox = row.querySelector('input[type=checkbox]');
        if (!status.ok) {
          stateEl.textContent = status.msg || 'nicht erreichbar';
          return;
        }
        const isOn = !!status['relay' + relay + '_state'];
        checkbox.checked = isOn;
        stateEl.textContent = isOn ? 'AN' : 'AUS';
      });
    }).catch(() => {
      document.querySelectorAll('.switch-row[data-board="' + board + '"] .state').forEach(
        el => el.textContent = 'Gerät nicht erreichbar');
    }));
  }
  return Promise.all(requests);
}

function toggleRelay(board, relay, checkbox) {
  fetch('/api/atom-toggle/' + board + '/' + relay, {method: 'POST'}).then(r => r.json()).then(() => {
    updateSwitchStatus();
  }).catch(() => {
    checkbox.checked = !checkbox.checked;  // Optimistischen Klick zurücknehmen
  });
}

function saveAtomSettings(boardIndex) {
  const enabled = document.querySelector('.atom-enabled[data-board="' + boardIndex + '"]').checked;
  const baseUrl = document.querySelector('.atom-base-url[data-board="' + boardIndex + '"]').value;
  fetch('/atom/save', {
    method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: 'board_index=' + boardIndex + '&enabled=' + (enabled ? '1' : '0') +
      '&base_url=' + encodeURIComponent(baseUrl),
  }).then(r => r.json()).then(() => {
    document.getElementById('atom-save-status-' + boardIndex).textContent =
      'Gespeichert - wirkt innerhalb ca. 1s ohne Neustart.';
  }).catch(() => {
    document.getElementById('atom-save-status-' + boardIndex).textContent = 'Gerät nicht erreichbar.';
  });
}

updateSwitchStatus();
pollEvery(updateSwitchStatus, 8000);
