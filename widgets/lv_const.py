"""
Ein paar LVGL-Konstanten als robuste Zahlenwerte statt `lv.ENUM.OPTION`,
weil dieses spezielle m5ui/lvgl-Binding (UIFlow2 v2.4.6, MicroPython
v1.27.0) manche Enums nicht als verschachtelte Klasse bereitstellt - auf
echter Hardware bestätigt fehlend: `lv.RADIUS`, `lv.ANIM`.

Die Zahlenwerte hier sind die offiziellen, seit Jahren stabilen
LVGL-Kernwerte (aus lvgl/src/misc/lv_area.h bzw. lv_anim.h) - unabhängig
davon, ob das jeweilige Python-Binding sie als Enum-Klasse exportiert.

Bewusst NICHT für alle lv.XXX.YYY-Konstanten im Projekt gemacht (z.B.
lv.EVENT.*, lv.ALIGN.* bleiben als normale Enum-Zugriffe) - nur für die
bestätigt fehlenden, um nicht spekulativ falsche Zahlenwerte für Enums
mit vielen/unklaren Mitgliedern einzusetzen.
"""

RADIUS_CIRCLE = 32767  # LV_RADIUS_CIRCLE
ANIM_OFF = 0           # LV_ANIM_OFF
ANIM_ON = 1            # LV_ANIM_ON
LABEL_LONG_WRAP = 0    # LV_LABEL_LONG_WRAP (erster Wert von lv_label_long_mode_t)
