#!/usr/bin/env python3
"""
png_to_lvgl.py - Wandelt eine PNG-Datei (mit Transparenz) in das rohe,
dekoder-freie RGB565A8-Format um, das das Logo-Widget auf dem Tab5
anzeigen kann (siehe screens/widget_catalog.py::_build_logo()).

Hintergrund: Diese UIFlow2/LVGL-Firmware hat KEINEN PNG-Decoder eingebaut
(bestätigt per diagnose_image.py) - lv.image kann aber rohe, bereits
entpackte Pixel-Daten anzeigen (bestätigt per diagnose_image_raw.py).
Dieses Skript läuft auf DEINEM Rechner (braucht Pillow: pip install
pillow), NICHT auf dem Tab5 selbst.

Ausgabeformat: 4-Byte-Header (Breite, Höhe als je 2-Byte little-endian)
gefolgt von RGB565-Pixeldaten, gefolgt von einem 8-Bit-Alpha-Kanal -
genau das Format, das _build_logo() erwartet (die Maße werden aus dem
Header gelesen, keine separate Breite/Höhe-Angabe im Web-UI nötig).

Empfohlene Ziel-Größen (siehe HANDOFF.md/Projekt-Chat für die Herleitung
aus dem 4x3-Raster):
  - Eine Kachel  (quadratisch): 160 x 160
  - Zwei Kacheln (länglich):    560 x 160

Benutzung:
    python3 png_to_lvgl.py mein_logo.png logo1.bin --size 160x160
    python3 png_to_lvgl.py mein_logo.png logo1.bin --size 560x160

Das Bild wird proportional in die Zielgröße eingepasst (KEINE Verzerrung)
und mit transparenten Rändern aufgefüllt, falls das Seitenverhältnis
nicht exakt passt - dein Logo behält also immer seine echten Proportionen.
"""
import argparse
import struct
import sys

try:
    from PIL import Image
except ImportError:
    print("Dieses Skript braucht Pillow: pip install pillow", file=sys.stderr)
    sys.exit(1)


def convert(input_path, output_path, target_w, target_h):
    img = Image.open(input_path).convert("RGBA")

    # Proportional einpassen (wie CSS "object-fit: contain") statt zu
    # verzerren - überschüssiger Platz bleibt transparent.
    scale = min(target_w / img.width, target_h / img.height)
    new_w = max(1, round(img.width * scale))
    new_h = max(1, round(img.height * scale))
    resized = img.resize((new_w, new_h), Image.LANCZOS)

    canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
    offset = ((target_w - new_w) // 2, (target_h - new_h) // 2)
    canvas.paste(resized, offset, resized)

    rgb565 = bytearray(target_w * target_h * 2)
    alpha = bytearray(target_w * target_h)
    pixels = canvas.load()
    i = 0
    for y in range(target_h):
        for x in range(target_w):
            r, g, b, a = pixels[x, y]
            val = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
            rgb565[i * 2] = val & 0xFF
            rgb565[i * 2 + 1] = (val >> 8) & 0xFF
            alpha[i] = a
            i += 1

    header = struct.pack("<HH", target_w, target_h)
    with open(output_path, "wb") as f:
        f.write(header)
        f.write(rgb565)
        f.write(alpha)

    total = len(header) + len(rgb565) + len(alpha)
    print("Geschrieben: %s (%dx%d, %d Bytes)" % (output_path, target_w, target_h, total))
    print("Auf den Tab5 kopieren mit:")
    print("  mpremote connect <PORT> resume cp %s :%s" % (output_path, output_path))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", help="Eingabe-PNG (mit Transparenz)")
    parser.add_argument("output", help="Ausgabe-Datei (.bin)")
    parser.add_argument("--size", required=True, help="Zielgröße als BREITExHOEHE, z.B. 160x160 oder 560x160")
    args = parser.parse_args()

    try:
        w_str, h_str = args.size.lower().split("x")
        target_w, target_h = int(w_str), int(h_str)
    except ValueError:
        print("Ungültiges --size Format, erwartet z.B. 160x160", file=sys.stderr)
        sys.exit(1)

    convert(args.input, args.output, target_w, target_h)


if __name__ == "__main__":
    main()
