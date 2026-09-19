#!/bin/sh
# Tab5 Magic Mirror - Module vorkompilieren (.py -> .mpy) und aufs Geraet laden.
#
# WARUM: MicroPython uebersetzt jede .py-Datei bei JEDEM Boot neu in Bytecode. Beim
# Tab5 sind das ~8 s (Boot-Log: 5,1 s Basis-Module + 1,2 s Sensoren + 1,9 s Screens).
# Vorkompilierte .mpy-Dateien werden nur noch geladen - deutlich schneller und mit
# weniger RAM-Spitze.
#
# WICHTIG: MicroPython bevorzugt eine .py-Datei vor der gleichnamigen .mpy!
# Deshalb loescht dieses Skript beim Deployen die alten .py-Dateien auf dem Geraet.
# main.py bleibt ein kleines .py (ruft nur "import app"); der eigentliche Code
# liegt vorkompiliert in app.mpy.
#
# Voraussetzung (einmalig):
#   pip install "mpy-cross==1.27.0.post2"      # passt zu MicroPython v1.27.0 (mpy v6.3)
#   mpremote installiert
#
# Benutzung (im Projekt-Hauptordner):
#   sh tools/build_mpy.sh build                 # nur kompilieren nach build_mpy/
#   sh tools/build_mpy.sh deploy /dev/ttyACM1   # kompilieren + aufs Geraet laden
#   sh tools/build_mpy.sh deploy /dev/ttyACM1 --dry-run   # nur anzeigen, was passieren wuerde
#   sh tools/build_mpy.sh rollback /dev/ttyACM1 # zurueck auf die .py-Dateien (loescht .mpy)
#
# Statische Dateien (static/*.js, *.css) und config.json werden NICHT angefasst -
# die laedst du wie bisher hoch.
set -e

MODE="${1:-build}"
PORT="${2:-/dev/ttyACM1}"
DRY=""
[ "$3" = "--dry-run" ] && DRY="1"
ROOT="$(pwd)"
OUT="$ROOT/build_mpy"
DEVROOT="/flash"

# Dateien, die NICHT vorkompiliert werden
skip() {
  case "$1" in
    ./tools/*|./build_mpy/*|./tests/*|./static/*) return 0 ;;
    */diagnose_*|*-old.py|*/__pycache__/*|./boot.py|./settings.py|./magic_mirror.py|./aida_sse_server.py) return 0 ;;
    ./main.py) return 0 ;;      # wird gesondert behandelt (app.py + Stub)
  esac
  return 1
}

files() { find . -name '*.py' | sort | while read -r f; do skip "$f" || echo "$f"; done; }

run() { if [ -n "$DRY" ]; then echo "  [dry-run] $*"; else "$@"; fi; }
MP() { run mpremote connect "$PORT" "$@"; }

build() {
  command -v python3 >/dev/null || { echo "python3 fehlt"; exit 1; }
  python3 -m mpy_cross --version >/dev/null 2>&1 || { echo 'mpy-cross fehlt: pip install "mpy-cross==1.27.0.post2"'; exit 1; }
  rm -rf "$OUT"; mkdir -p "$OUT"
  n=0
  for f in $(files); do
    rel="${f#./}"; mkdir -p "$OUT/$(dirname "$rel")"
    python3 -m mpy_cross -s "$rel" -o "$OUT/${rel%.py}.mpy" "$f"; n=$((n+1))
  done
  if [ -f main.py ]; then
    cp main.py "$OUT/app_src.py"
    python3 -m mpy_cross -s main.py -o "$OUT/app.mpy" "$OUT/app_src.py"; rm "$OUT/app_src.py"
    if [ -f tools/main_stub.py ]; then
      cp tools/main_stub.py "$OUT/main.py"     # Startskript mit UIFlow2-Modus (siehe tools/main_stub.py)
    else
      printf '# Startskript: der eigentliche Code liegt vorkompiliert in app.mpy (siehe tools/build_mpy.sh)\nimport app\n' > "$OUT/main.py"
    fi
    n=$((n+1))
  fi
  echo "$n Dateien nach build_mpy/ kompiliert ($(python3 -m mpy_cross --version 2>&1 | head -1))"
}

case "$MODE" in
  build) build ;;
  deploy)
    build
    echo "--- mpy-Format der Firmware pruefen (erwartet: 'mpy-Format: 6 . 3') ---"
    MP exec "import sys; v=sys.implementation._mpy; print('mpy-Format:', v & 0xff, '.', (v >> 8) & 3)"
    dirs=$(cd "$OUT" && find . -name '*.mpy' | while read -r f; do dirname "$f"; done | sort -u)
    for d in $dirs; do [ "$d" = "." ] || MP fs mkdir ":$DEVROOT/${d#./}" 2>/dev/null || true; done
    (cd "$OUT" && find . -name '*.mpy' | sort) | while read -r f; do
      rel="${f#./}"
      # alte .py auf dem Geraet entfernen (sonst gewinnt sie vor der .mpy)
      MP fs rm ":$DEVROOT/${rel%.mpy}.py" 2>/dev/null || true
      MP fs cp "$OUT/$rel" ":$DEVROOT/$rel"
    done
    MP fs cp "$OUT/main.py" ":$DEVROOT/main.py"
    echo "Fertig. Geraet neu starten (Kabel raus/rein oder mpremote reset) und den Boot-Log ansehen."
    ;;
  rollback)
    echo "Lade die .py-Dateien wieder hoch und loesche die .mpy-Dateien ..."
    for f in $(files) ./main.py; do
      rel="${f#./}"
      MP fs cp "$f" ":$DEVROOT/$rel"
      MP fs rm ":$DEVROOT/${rel%.py}.mpy" 2>/dev/null || true
    done
    MP fs rm ":$DEVROOT/app.mpy" 2>/dev/null || true
    echo "Fertig - Geraet neu starten."
    ;;
  *) echo "Benutzung: sh tools/build_mpy.sh build|deploy|rollback [PORT] [--dry-run]"; exit 1 ;;
esac
