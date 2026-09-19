"""
Eigenstaendiger Diagnose-Test fuer lv.dropdown.add_event_cb - laeuft
UNABHAENGIG vom Hauptprogramm. Hintergrund: dieselbe add_event_cb(cb,
event_code, user_data)-Aufrufform funktioniert bei lv.button/lv.switch/
lv.slider einwandfrei, aber nicht bei lv.dropdown (TypeError: function
takes 3 positional arguments but 2 were given) - selbst mit Lambda-
Wrapper um den Callback. Testet hier mehrere Varianten isoliert.
"""
import M5
import lvgl as lv

M5.begin()
page = lv.obj()
dd = lv.dropdown(page)
dd.set_options("A\nB\nC")

print("dir(dd) enthaelt add_event_cb:", "add_event_cb" in dir(dd))
print()

# Variante 1: wie bisher (cb, event_code, user_data)
try:
    dd.add_event_cb(lambda e: None, lv.EVENT.VALUE_CHANGED, None)
    print("Variante 1 (cb, event_code, None) -> OK")
except Exception as e:
    print("Variante 1 (cb, event_code, None) -> FEHLER:", e)

# Variante 2: ohne user_data
try:
    dd.add_event_cb(lambda e: None, lv.EVENT.VALUE_CHANGED)
    print("Variante 2 (cb, event_code) -> OK")
except Exception as e:
    print("Variante 2 (cb, event_code) -> FEHLER:", e)

# Variante 3: nur der Callback
try:
    dd.add_event_cb(lambda e: None)
    print("Variante 3 (nur cb) -> OK")
except Exception as e:
    print("Variante 3 (nur cb) -> FEHLER:", e)

# Zum Vergleich: dieselben drei Varianten an einem lv.button (der
# nachweislich funktioniert) - falls sich DAS jetzt auch anders verhaelt,
# ist es kein dropdown-spezifisches Problem.
btn = lv.button(page)
try:
    btn.add_event_cb(lambda e: None, lv.EVENT.CLICKED, None)
    print("Button-Vergleich (cb, event_code, None) -> OK")
except Exception as e:
    print("Button-Vergleich (cb, event_code, None) -> FEHLER:", e)

print()
print("Fertig. Bitte den kompletten Log zurückmelden.")
