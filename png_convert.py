"""
Reiner Python-PNG-Decoder + Umwandler ins RGB565A8-Format, das
screens/widget_catalog.py::_build_logo() erwartet (Optimierungs-Backlog
Punkt 7, siehe HANDOFF.md) - macht tools/png_to_lvgl.py + mpremote für
das Logo-Widget überflüssig, siehe web_server.py::"/api/upload-logo".

Ziel-Binärformat (bestätigt aus screens/widget_catalog.py::_build_logo()):
    Byte 0-1: Breite  (uint16, little-endian)
    Byte 2-3: Höhe    (uint16, little-endian)
    danach:   Breite*Höhe*2 Bytes RGB565-Pixeldaten (little-endian je Pixel)
    danach:   Breite*Höhe Bytes Alpha (1 Byte je Pixel, 8-bit)

UNTERSTÜTZT: 8-bit-PNGs, nicht interlaced, Farbtypen 0 (Graustufen),
2 (RGB), 3 (Palette/PLTE, optional tRNS), 4 (Graustufen+Alpha), 6 (RGBA) -
das deckt praktisch jedes PNG ab, das eine normale Export-Funktion
("Für Web speichern" o.ä.) erzeugt.

NICHT UNTERSTÜTZT (bewusst, statt eine riskante Teillösung zu bauen):
16-bit-Farbtiefe, Adam7-Interlacing - beides eher selten bei Logos/Icons.
Bei Bedarf meldet convert() eine klare Fehlermeldung statt eines falschen
Ergebnisses, die zum erneuten Export mit anderen Einstellungen anleitet.

KEINE Skalierung eingebaut - ein zu großes Bild wird mit einer klaren
Fehlermeldung abgelehnt (siehe MAX_WIDTH/MAX_HEIGHT), statt eine
selbstgeschriebene, schwer zu verifizierende Resampling-Logik zu riskieren.
Der Nutzer verkleinert das Bild stattdessen vorher selbst (jedes simple
Bildbearbeitungsprogramm/jede Webseite kann das).

UNVERIFIZIERT AUF ECHTER HARDWARE (siehe generelle "erst per Diagnose-
Skript prüfen"-Regel für neue APIs in diesem Projekt, HANDOFF.md Abschnitt
2): welches Dekompressions-Modul (zlib/uzlib/deflate) auf dieser
MicroPython-Firmware tatsächlich verfügbar ist - _inflate() unten
versucht alle drei Varianten der Reihe nach. Die eigentliche PNG-Decoder-
Logik (Chunk-Parsing, Entfiltern, Palette/RGBA-Umrechnung) wurde gegen
Pillow als Referenz-Decoder getestet (siehe tools/test_png_convert.py,
läuft nur unter Desktop-Python) und ist firmware-unabhängig, da reine
Byte-/Array-Arithmetik ohne Abhängigkeit von einer Bild-Bibliothek.
"""

import struct

MAX_WIDTH = 560   # passend zu tools/png_to_lvgl.py-Konvention "--size 560x160" (2 Kacheln breit)
MAX_HEIGHT = 320  # großzügiger Puffer über die "160" bzw. "560x160"-Beispiele aus web_server.py hinaus

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _read_bounded(stream, max_out):
    """Liest aus einem Dekompressions-Stream hoechstens max_out+1 Bytes (ein Byte
    mehr, damit ein Ueberschreiten erkennbar ist)."""
    out = bytearray()
    while len(out) <= max_out:
        chunk = stream.read(4096)
        if not chunk:
            break
        out += chunk
    return bytes(out)


def _inflate(data, max_out):
    """Zlib-komprimierte PNG-IDAT-Daten dekomprimieren - HOECHSTENS max_out Bytes
    (die aus IHDR berechnete Erwartung). Frueher unbegrenzt: eine winzige Datei
    kann zu hunderten MB entpacken ("Dekompressionsbombe") und den RAM sprengen.
    Probiert die auf verschiedenen MicroPython-Firmwares ueblichen Module durch;
    deflate/uzlib lesen gestreamt und brechen ab, zlib.decompress() (nur wenn
    nichts anderes da ist) kann nicht begrenzen - dort greift nur die
    Nachpruefung."""
    out = None
    try:
        import deflate
        import io
        out = _read_bounded(deflate.DeflateIO(io.BytesIO(data), deflate.ZLIB), max_out)
    except ImportError:
        pass
    if out is None:
        try:
            import uzlib
            import io
            out = _read_bounded(uzlib.DecompIO(io.BytesIO(data), 15), max_out)  # 15 = zlib-Header
        except ImportError:
            pass
    if out is None:
        try:
            import zlib
        except ImportError:
            raise ValueError("Keine zlib/uzlib/deflate-Dekompression auf dieser Firmware verfügbar - "
                              "PNG-Upload kann hier nicht funktionieren.")
        if hasattr(zlib, "decompressobj"):   # CPython (Desktop-Tests): begrenzbar
            out = zlib.decompressobj().decompress(data, max_out + 1)
        else:
            out = zlib.decompress(data)
    if len(out) > max_out:
        raise ValueError("Bilddaten sind größer als erwartet - Datei beschädigt oder ungültig.")
    return out


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _unfilter(data, width, height, bpp):
    """Entfernt die PNG-Scanline-Filter (None/Sub/Up/Average/Paeth) -
    siehe PNG-Spezifikation §9. bpp = Bytes pro Pixel (für die Filter
    relevant, NICHT dasselbe wie die spätere Kanalzahl bei Paletten-
    Bildern, wo bpp=1 ist, obwohl am Ende 3-4 Kanäle rauskommen)."""
    stride = width * bpp
    out = bytearray(stride * height)
    pos = 0
    prev_row = bytearray(stride)
    for y in range(height):
        filter_type = data[pos]
        pos += 1
        row = bytearray(data[pos:pos + stride])
        pos += stride
        if filter_type == 0:
            pass
        elif filter_type == 1:  # Sub
            for i in range(bpp, stride):
                row[i] = (row[i] + row[i - bpp]) & 0xFF
        elif filter_type == 2:  # Up
            for i in range(stride):
                row[i] = (row[i] + prev_row[i]) & 0xFF
        elif filter_type == 3:  # Average
            for i in range(stride):
                a = row[i - bpp] if i >= bpp else 0
                b = prev_row[i]
                row[i] = (row[i] + ((a + b) >> 1)) & 0xFF
        elif filter_type == 4:  # Paeth
            for i in range(stride):
                a = row[i - bpp] if i >= bpp else 0
                b = prev_row[i]
                c = prev_row[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + _paeth(a, b, c)) & 0xFF
        else:
            raise ValueError("Unbekannter PNG-Filtertyp %d (Zeile %d) - Datei beschädigt?" % (filter_type, y))
        out[y * stride:(y + 1) * stride] = row
        prev_row = row
    return bytes(out)


def _parse_chunks(png_bytes):
    if png_bytes[:8] != PNG_SIGNATURE:
        raise ValueError("Keine gültige PNG-Datei (Signatur fehlt).")
    pos = 8
    chunks = []
    n = len(png_bytes)
    while pos < n:
        if pos + 8 > n:
            break
        length = struct.unpack(">I", png_bytes[pos:pos + 4])[0]
        ctype = png_bytes[pos + 4:pos + 8]
        data_start = pos + 8
        if data_start + length > n:
            break  # Chunk laenger als die Datei - abgeschnitten/beschaedigt
        data = png_bytes[data_start:data_start + length]
        chunks.append((ctype, data))
        pos = data_start + length + 4  # +4 = CRC überspringen, wird nicht geprüft
        if ctype == b"IEND":
            break
    return chunks


def convert(png_bytes, max_width=MAX_WIDTH, max_height=MAX_HEIGHT):
    """Wandelt PNG-Bytes in das RGB565A8-Binärformat für das Logo-Widget
    um (siehe Modul-Docstring). Wirft ValueError mit einer für den
    Endnutzer verständlichen Meldung bei jedem nicht unterstützten Fall -
    NIE ein stilles Fehlschlagen oder ein falsches Ergebnis."""
    chunks = _parse_chunks(png_bytes)
    ihdr = next((d for t, d in chunks if t == b"IHDR"), None)
    if ihdr is None:
        raise ValueError("PNG ohne IHDR-Chunk - Datei beschädigt?")

    if len(ihdr) < 13:
        raise ValueError("PNG-Kopf (IHDR) ist beschädigt.")
    width, height, bit_depth, color_type, compression, filter_method, interlace = \
        struct.unpack(">IIBBBBB", ihdr[:13])

    if width == 0 or height == 0:
        raise ValueError("Ungültige Bildgröße (%dx%d)." % (width, height))
    if compression != 0 or filter_method != 0:
        raise ValueError("Ungültiges PNG (Kompressions-/Filtermethode).")

    if width > max_width or height > max_height:
        raise ValueError(
            "Bild zu groß (%dx%d, erlaubt max. %dx%d) - bitte vorher verkleinern."
            % (width, height, max_width, max_height))
    if bit_depth != 8:
        raise ValueError(
            "Nur 8-bit-PNGs werden unterstützt (dieses Bild hat %d bit) - "
            "beim Export z.B. 'Graustufen/Farbe, 8 Bit pro Kanal' wählen." % bit_depth)
    if interlace != 0:
        raise ValueError("Interlaced PNGs (Adam7) werden nicht unterstützt - "
                          "bitte ohne 'Interlacing'/'Progressive' erneut exportieren.")
    if color_type not in (0, 2, 3, 4, 6):
        raise ValueError("Nicht unterstützter PNG-Farbtyp %d." % color_type)

    palette = next((d for t, d in chunks if t == b"PLTE"), None)
    trns = next((d for t, d in chunks if t == b"tRNS"), None)
    idat = b"".join(d for t, d in chunks if t == b"IDAT")
    if not idat:
        raise ValueError("PNG ohne Bilddaten (kein IDAT-Chunk).")

    channels_by_type = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
    channels = channels_by_type[color_type]
    expected = height * (width * channels + 1)   # je Zeile 1 Filterbyte + Pixeldaten
    raw = _inflate(idat, expected)
    if len(raw) != expected:
        raise ValueError("Bilddaten unvollständig oder beschädigt (%d statt %d Bytes)." % (len(raw), expected))
    unfiltered = _unfilter(raw, width, height, channels)

    # ---- Auf RGBA8 (4 Bytes je Pixel) vereinheitlichen ----
    rgba = bytearray(width * height * 4)
    if color_type == 2:  # RGB
        # tRNS bei RGB = ein "durchsichtiger" Farbwert (je 2 Bytes pro Kanal, 8-bit: niederwertiges Byte)
        key = (trns[1], trns[3], trns[5]) if trns is not None and len(trns) >= 6 else None
        for i in range(width * height):
            src = i * 3
            dst = i * 4
            rgba[dst:dst + 3] = unfiltered[src:src + 3]
            if key is not None and unfiltered[src] == key[0] and unfiltered[src + 1] == key[1] \
                    and unfiltered[src + 2] == key[2]:
                rgba[dst + 3] = 0
            else:
                rgba[dst + 3] = 255
    elif color_type == 6:  # RGBA
        rgba[:] = unfiltered
    elif color_type == 0:  # Graustufen
        key = trns[1] if trns is not None and len(trns) >= 2 else None
        for i in range(width * height):
            g = unfiltered[i]
            dst = i * 4
            rgba[dst] = rgba[dst + 1] = rgba[dst + 2] = g
            rgba[dst + 3] = 0 if (key is not None and g == key) else 255
    elif color_type == 4:  # Graustufen + Alpha
        for i in range(width * height):
            src = i * 2
            dst = i * 4
            g = unfiltered[src]
            rgba[dst] = rgba[dst + 1] = rgba[dst + 2] = g
            rgba[dst + 3] = unfiltered[src + 1]
    elif color_type == 3:  # Palette
        if palette is None:
            raise ValueError("Palettenbild ohne PLTE-Chunk - Datei beschädigt?")
        palette_size = len(palette) // 3
        for i in range(width * height):
            idx = unfiltered[i]
            if idx >= palette_size:
                raise ValueError("Ungültiger Palettenindex %d (Palette hat %d Einträge)." % (idx, palette_size))
            pr, pg, pb = palette[idx * 3:idx * 3 + 3]
            dst = i * 4
            rgba[dst] = pr
            rgba[dst + 1] = pg
            rgba[dst + 2] = pb
            rgba[dst + 3] = trns[idx] if trns is not None and idx < len(trns) else 255

    # ---- RGBA8 -> RGB565 (2 Byte) + separates A8 (1 Byte) ----
    rgb565 = bytearray(width * height * 2)
    alpha = bytearray(width * height)
    for i in range(width * height):
        base = i * 4
        r, g, b, a = rgba[base], rgba[base + 1], rgba[base + 2], rgba[base + 3]
        value = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
        rgb565[i * 2] = value & 0xFF
        rgb565[i * 2 + 1] = (value >> 8) & 0xFF
        alpha[i] = a

    header = bytes([width & 0xFF, (width >> 8) & 0xFF, height & 0xFF, (height >> 8) & 0xFF])
    return header + bytes(rgb565) + bytes(alpha)
