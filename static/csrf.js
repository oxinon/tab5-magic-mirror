// CSRF-Schutz (Optimierungs-Backlog Punkt 6, siehe HANDOFF.md) - patcht
// window.fetch() EINMALIG, BEVOR irgendein Seiten-eigenes Skript weiter
// unten seine fetch()-Aufrufe definiert, damit jeder bestehende POST-
// Aufruf in JEDER Seite automatisch den Header mitbekommt, ohne dass an
// den vielen einzelnen fetch()-Stellen selbst etwas geändert werden musste.
// Ausgelagert aus einem Python-String (Optimierungs-Backlog Punkt 5/7,
// siehe HANDOFF.md) - erwartet window.CSRF_TOKEN, gesetzt durch ein
// winziges Inline-Script direkt VOR dieser Datei im HTML (der einzige
// pro Boot wirklich dynamische Wert hier, siehe web_server.py::
// CSRF_TOKEN/_generate_csrf_token()).
// HTML-Escaping fuer ALLES, was per innerHTML eingesetzt wird und aus
// fremder Quelle stammt (RSS-Titel, Kalendertermine, Container-Namen,
// Fehlermeldungen, WLAN-Namen, ...) - sonst kann so ein Inhalt Skripte auf
// dieser Seite ausfuehren (dort ist das CSRF-Token bekannt -> Reboot, WLAN,
// Config aenderbar). Diese Datei wird auf JEDER Seite vor allen anderen
// Skripten geladen, daher steht die Funktion hier.
function escHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// Regelmaessiges Abfragen (Polling) ohne den Tab5 unnoetig zu belasten:
//  - pausiert, solange der Browser-Tab nicht sichtbar ist (Hintergrund-Tabs
//    haben bisher weitergepollt - jede offene Seite = Dauerlast fuer das Geraet),
//  - startet keinen neuen Durchlauf, solange der vorherige noch laeuft (sonst
//    stauen sich Anfragen bei einem langsamen/nicht erreichbaren Geraet),
//  - holt beim Zurueckkehren in den Tab sofort frische Daten.
// fn soll ein Promise zurueckgeben (z.B. das fetch-Ergebnis).
function pollEvery(fn, ms) {
  let running = false;
  function tick() {
    if (document.hidden || running) return;
    running = true;
    Promise.resolve().then(fn).catch(() => {}).then(() => { running = false; });
  }
  setInterval(tick, ms);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) tick(); });
}

const _originalFetch = window.fetch;
window.fetch = function(input, init) {
  init = init || {};
  if ((init.method || 'GET').toUpperCase() === 'POST') {
    init.headers = Object.assign({}, init.headers, {'X-CSRF-Token': window.CSRF_TOKEN});
  }
  return _originalFetch(input, init);
};
