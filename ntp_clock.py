"""
NTP-Zeitsync + lokale Zeitberechnung inkl. automatischer EU-Sommerzeit
(letzter Sonntag im März 01:00 UTC bis letzter Sonntag im Oktober 01:00 UTC).

1:1 portiert aus dem vom Nutzer bereitgestellten Core2-MiniDash-
Referenzprojekt (dort bereits produktiv im Einsatz) - unverändert
übernommen, da die Logik hardware-unabhängig ist (reine Zeitrechnung,
kein Sensor-/Display-Zugriff).

Passt für Deutschland (utc_offset=1 in config.json "general" = MEZ). Für
andere Zeitzonen ohne EU-Sommerzeit-Regel "dst_auto": false setzen und
"utc_offset" von Hand pflegen.

WICHTIG: MicroPythons `time.localtime()`/`time.mktime()` arbeiten intern
mit UTC (keine Zeitzone gesetzt) - `sync()` setzt die interne RTC direkt
auf UTC (das macht `ntptime.settime()` so), und erst `local_time()`/
`local_time_from_epoch()` unten rechnen den konfigurierten Offset +
Sommerzeit-Korrektur dazu. Wer stattdessen direkt `time.localtime()`
aufruft (z.B. beim Debuggen im REPL), sieht also UTC, nicht Ortszeit -
das ist kein Bug, sondern MicroPythons übliches Verhalten.
"""

import time

_synced = False

# Für Aufrufer ohne direkten Zugriff auf das komplette config.json-Dict
# (z.B. widgets/clock_widget.py) - siehe set_general()/now_local() unten.
_general_cfg = {"utc_offset": 1, "dst_auto": True}


def set_general(general_cfg):
    """Von main.py einmal beim Boot mit cfg["general"] aufgerufen (und
    erneut, falls sich die Einstellung zur Laufzeit ändert)."""
    global _general_cfg
    if general_cfg:
        _general_cfg = general_cfg


def now_local():
    """Bequemlichkeitsfunktion: Ortszeit (inkl. Sommerzeit) basierend auf
    den zuletzt über set_general() gesetzten Werten, ohne dass der
    Aufrufer selbst das cfg-Dict durchreichen muss."""
    return local_time_from_epoch(time.time(), _general_cfg)


def from_epoch(t_utc):
    """Wie now_local(), aber für einen beliebigen UTC-Epoch-Zeitstempel
    statt der aktuellen Zeit - z.B. für Kalendertermine (siehe
    screens/widget_catalog.py::_format_event). Nutzt ebenfalls die zuletzt
    über set_general() gesetzten Werte."""
    return local_time_from_epoch(t_utc, _general_cfg)


def sync(retries=3):
    """Einmalig per NTP synchronisieren. Gibt True/False zurück. Braucht
    eine bestehende WLAN-Verbindung (Station-Modus) - im Access-Point-/
    Setup-Modus ohne Internetzugang schlägt das erwartungsgemäß fehl."""
    global _synced
    import ntptime
    ntptime.host = "pool.ntp.org"
    for _ in range(retries):
        try:
            ntptime.settime()
            _synced = True
            return True
        except Exception:
            time.sleep(1)
    return False


def is_synced():
    return _synced


def _days_in_month(y, m):
    if m in (1, 3, 5, 7, 8, 10, 12):
        return 31
    if m in (4, 6, 9, 11):
        return 30
    if (y % 4 == 0 and y % 100 != 0) or y % 400 == 0:
        return 29
    return 28


def _last_sunday(y, m):
    d = _days_in_month(y, m)
    t = time.mktime((y, m, d, 1, 0, 0, 0, 0))
    wd = time.localtime(t)[6]  # 0=Montag .. 6=Sonntag
    return d - ((wd + 1) % 7)


def _is_dst_eu(y, m, d, hh):
    if m < 3 or m > 10:
        return False
    if 3 < m < 10:
        return True
    ls = _last_sunday(y, m)
    if m == 3:
        if d < ls:
            return False
        if d > ls:
            return True
        return hh >= 1
    else:
        if d < ls:
            return True
        if d > ls:
            return False
        return hh < 1


def local_time(cfg):
    """Gibt ein time.localtime()-Tupel in Ortszeit zurück. cfg: das
    komplette config.json-Dict (nutzt cfg["general"])."""
    general = cfg.get("general", {})
    t_utc = time.time()
    return local_time_from_epoch(t_utc, general), _dst_for_epoch(t_utc, general)


def _dst_for_epoch(t_utc, general):
    dst_auto = general.get("dst_auto", True)
    if not dst_auto:
        return False
    lt = time.localtime(t_utc)
    return _is_dst_eu(lt[0], lt[1], lt[2], lt[3])


def local_time_from_epoch(t_utc, general):
    """Wie local_time(), nimmt aber einen beliebigen UTC-Epoch-Zeitstempel
    entgegen statt der aktuellen Zeit - z.B. für Kalendertermine."""
    utc_offset = general.get("utc_offset", 1)
    dst = _dst_for_epoch(t_utc, general)
    offset_h = utc_offset + (1 if dst else 0)
    return time.localtime(t_utc + offset_h * 3600)


def local_wall_to_utc_epoch(y, mo, d, hh, mi, ss, general):
    """Kehrfunktion zu local_time_from_epoch: rechnet lokale Wanduhrzeit-
    Werte (z.B. aus einem iCal-Termin ohne UTC-Kennung) in einen UTC-Epoch
    zurück - DST-Näherung anhand des Kalenderdatums selbst."""
    utc_offset = general.get("utc_offset", 1)
    dst_auto = general.get("dst_auto", True)
    dst = _is_dst_eu(y, mo, d, hh) if dst_auto else False
    offset_h = utc_offset + (1 if dst else 0)
    naive = time.mktime((y, mo, d, hh, mi, ss, 0, 0))
    return naive - offset_h * 3600
