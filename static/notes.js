// JS für die Notizen-Seite ("/notes", siehe web_server.py::
// _notes_page_html()/NOTES_BODY). Ausgelagert aus einem Python-String
// (Optimierungs-Backlog Punkt 5/7, siehe HANDOFF.md). Die Obergrenze der
// Notizen (MAX_TODO_ITEMS in web_server.py) kommt als data-max-Attribut
// von #notes-count - hier kein fester Wert.

function maxNotes() {
  const el = document.getElementById('notes-count');
  return el ? parseInt(el.dataset.max, 10) || 20 : 20;
}

function setNotesStatus(text) {
  document.getElementById('notes-save-status').textContent = text;
}

// Liefert ein Promise<boolean> (true = gespeichert), damit Aufrufer, die
// danach neu laden, erst NACH der fertigen Antwort neu laden (sonst kann
// location.reload() die laufende Anfrage abbrechen).
function saveNotes(items) {
  return fetch('/notes/save', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({items: items}),
  }).then(r => r.json().then(data => ({ok: r.ok && data.ok, data: data}))).then(res => {
    if (res.ok) {
      setNotesStatus('Gespeichert um ' + new Date().toLocaleTimeString() +
                     ' - wirkt innerhalb ca. 1s ohne Neustart.');
    } else {
      setNotesStatus((res.data && res.data.msg) || 'Speichern fehlgeschlagen.');
    }
    return res.ok;
  }).catch(() => {
    setNotesStatus('Gerät nicht erreichbar.');
    return false;
  });
}

function collectNotes() {
  const items = [];
  document.querySelectorAll('.note-row').forEach(row => {
    items.push({
      text: row.querySelector('.note-text').value,
      done: row.querySelector('input[type=checkbox]').checked,
    });
  });
  return items;
}

function toggleNote(index, checkboxEl) {
  saveNotes(collectNotes());
}

function editNoteText(index, inputEl) {
  saveNotes(collectNotes());
}

function deleteNote(index) {
  const items = collectNotes();
  items.splice(index, 1);
  saveNotes(items).then(ok => { if (ok) location.reload(); });
}

function addNote() {
  const input = document.getElementById('new-note-text');
  const text = input.value.trim();
  if (!text) return;
  const items = collectNotes();
  if (items.length >= maxNotes()) {
    setNotesStatus('Maximal ' + maxNotes() + ' Notizen möglich - bitte zuerst eine löschen.');
    return;
  }
  items.push({text: text, done: false});
  saveNotes(items).then(ok => {
    if (ok) { input.value = ''; location.reload(); }
  });
}
