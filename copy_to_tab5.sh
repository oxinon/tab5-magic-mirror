#!/bin/bash
# Kopiert alle Projektdateien auf die Tab5 - nutzt überall "resume",
# damit kein Reset ausgelöst wird (der würde den bekannten Launcher-Crash
# auf der aktuellen Firmware wieder auslösen).
#
# WICHTIG: Vor dem Ausführen muss das Gerät bereits in einem stabilen
# Zustand sein (z.B. per "mpremote connect $PORT" + Strg-C abgefangen,
# dann mit Strg-] sauber verlassen - siehe Chat-Verlauf).
#
# Aufruf: bash copy_to_tab5.sh [PORT]
# Beispiel: bash copy_to_tab5.sh /dev/ttyACM1

set -e  # bei einem echten Fehler abbrechen, statt stumm weiterzumachen

PORT="${1:-/dev/ttyACM1}"
MP="mpremote connect $PORT resume"

echo ">>> Verzeichnisse anlegen (Fehler falls schon vorhanden sind ok)"
$MP mkdir sensors || true
$MP mkdir screens || true
$MP mkdir widgets || true
$MP mkdir static || true

echo ">>> Basis-Dateien kopieren"
$MP cp config.py :
$MP cp theme.py :
$MP cp i18n.py :
$MP cp ascii_text.py :
$MP cp ntp_clock.py :
$MP cp widget_sources.py :
$MP cp fetch_worker.py :
$MP cp wifi_manager.py :
$MP cp api_client.py :
$MP cp ha_client.py :
$MP cp atom_client.py :
$MP cp web_server.py :
$MP cp burger_menu.py :
$MP cp png_convert.py :

$MP cp main.py :main.py
$MP cp lvgl_safety.py :lvgl_safety.py

echo ">>> static web/ kopieren"
$MP cp static/chart.min.js :static/chart.min.js
$MP cp static/style.css :static/style.css
$MP cp static/start.js :static/start.js
$MP cp static/sensors.js :static/sensors.js
$MP cp static/dashboard.js :static/dashboard.js
$MP cp static/switches.js :static/switches.js
$MP cp static/system.js :static/system.js
$MP cp static/csrf.js :static/csrf.js
$MP cp static/notes.js :static/notes.js

echo ">>> sensors/ kopieren"
$MP cp sensors/accelerometer.py :sensors/
$MP cp sensors/air_quality.py :sensors/
$MP cp sensors/bme680_driver.py :sensors/
$MP cp sensors/history.py :sensors/
$MP cp sensors/iaq_tracker.py :sensors/
$MP cp sensors/microphone.py :sensors/
$MP cp sensors/quake_trigger.py :sensors/
$MP cp sensors/sd_logger.py :sensors/
$MP cp sensors/sd_reader.py :sensors/

echo ">>> screens/ kopieren"
$MP cp screens/dashboard.py :screens/
$MP cp screens/sensor_history_screen.py :screens/
$MP cp screens/widget_catalog.py :screens/

echo ">>> widgets/ kopieren"
$MP cp widgets/lv_const.py :widgets/
$MP cp widgets/acceleration_widget.py :widgets/
$MP cp widgets/air_quality_light.py :widgets/
$MP cp widgets/card.py :widgets/
$MP cp widgets/clock_widget.py :widgets/
$MP cp widgets/equalizer.py :widgets/
$MP cp widgets/grid_layout.py :widgets/
$MP cp widgets/level_meter.py :widgets/
$MP cp widgets/quake_indicator.py :widgets/
$MP cp widgets/sound_light.py :widgets/
$MP cp widgets/sparkline.py :widgets/
$MP cp widgets/status_bar.py :widgets/
$MP cp widgets/traffic_light.py :widgets/

echo ">>> Fertig! Alle Dateien kopiert."
