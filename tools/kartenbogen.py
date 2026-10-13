#!/usr/bin/env python3
"""Setzt Album-Cover als Etiketten für die Musikkarten (HERMA 5028, 83,8 x 50,8 mm).

  kartenbogen.py ORDNER [-o karten.pdf] [--layout quer|hoch] [--start N] [--kopien N]
  kartenbogen.py --probe [-o probe.pdf]

ORDNER enthält die Cover (JPEG, PNG, WebP). Der Dateiname ist der Text auf dem Etikett:
„Puderzucker - Folge 3.jpg“ wird zu Titel „Puderzucker“ und Untertitel „Folge 3“. Eine
Nummer vorne („01 Puderzucker.jpg“) legt nur die Reihenfolge fest und wird nicht gedruckt.

Die Grundfarbe jedes Etiketts kommt aus dem Cover, die Schrift wird hell oder dunkel, je
nachdem, was darauf besser lesbar ist.

Drucken: „Tatsächliche Größe“ bzw. 100 %, nicht „An Seite anpassen“. Vorher einmal
--probe auf Normalpapier drucken, gegen das Licht auf einen Etikettenbogen legen und den
Versatz mit --versatz-x/--versatz-y (mm, positiv = nach rechts/unten) ausgleichen.
"""

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

SCHRIFTEN = Path(__file__).resolve().parent / "schriften"
DPI = 300
MM = DPI / 25.4
A4 = (round(210 * MM), round(297 * MM))
BILDER = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass(frozen=True)
class Bogen:
    """Maße eines Etikettenbogens in mm."""

    breite: float
    hoehe: float
    spalten: int
    zeilen: int
    rand_links: float
    rand_oben: float
    schritt_x: float
    schritt_y: float
    radius: float


# Maße aus der gLabels-Vorlage „Herma 5028“
HERMA_5028 = Bogen(83.8, 50.8, 2, 5, 18.6, 22.0, 89.2, 50.8, 2.0)

# Beschnittzugabe: so weit läuft die Farbe über den Etikettenrand. Nur seitlich, weil
# die Etiketten übereinander ohne Abstand sitzen und sonst ins Nachbaretikett malen.
BESCHNITT_X = 1.5


def px(mm: float) -> int:
    return round(mm * MM)


def schrift(name: str, pt: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(SCHRIFTEN / name), round(pt * DPI / 72))


def titel_aus_datei(pfad: Path) -> tuple[str, str]:
    name = re.sub(r"^\d+[\s._-]+", "", pfad.stem).replace("_", " ").strip()
    titel, _, untertitel = name.partition(" - ")
    return titel.strip(), untertitel.strip()


def grundfarbe(bild: Image.Image) -> tuple[int, int, int]:
    """Kräftigste häufige Farbe des Covers, leicht gedämpft."""
    klein = bild.convert("RGB").resize((64, 64))
    palette = klein.quantize(colors=6, method=Image.Quantize.MEDIANCUT)
    farben = palette.getpalette() or []
    beste, wertung = (120, 110, 100), -1.0
    for anzahl, index in palette.getcolors() or []:
        r, g, b = farben[index * 3 : index * 3 + 3]
        hoch, tief = max(r, g, b), min(r, g, b)
        saettigung = (hoch - tief) / hoch if hoch else 0
        # sehr helle und sehr dunkle Flächen (Ränder, Schrift) zählen weniger
        helligkeit = (r + g + b) / 765
        mitte = 1 - abs(helligkeit - 0.5) * 1.2
        w = anzahl * (0.35 + saettigung) * mitte
        if w > wertung:
            beste, wertung = (r, g, b), w
    grau = sum(beste) / 3
    return tuple(round(c * 0.85 + grau * 0.15) for c in beste)  # type: ignore[return-value]


def textfarben(farbe: tuple[int, int, int]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    def kanal(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (kanal(c) for c in farbe)
    luminanz = 0.2126 * r + 0.7152 * g + 0.0722 * b
    if luminanz > 0.3:
        return (43, 37, 32), (43, 37, 32, 170)
    return (255, 252, 246), (255, 252, 246, 190)


def umbrechen(
    zeichnen: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, breite: int
) -> list[str]:
    zeilen: list[str] = []
    for wort in text.split():
        if zeilen and zeichnen.textlength(f"{zeilen[-1]} {wort}", font=font) <= breite:
            zeilen[-1] += f" {wort}"
        else:
            zeilen.append(wort)
    return zeilen


def textblock(
    zeichnen: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    titel: str,
    untertitel: str,
    farben: tuple[tuple[int, ...], tuple[int, ...]],
    *,
    max_pt: float,
) -> None:
    """Titel und Untertitel, linksbündig, vertikal mittig; Titel schrumpft bis er passt."""
    x0, y0, x1, y1 = box
    farbe, farbe2 = farben
    breite, hoehe = x1 - x0, y1 - y0
    pt = max_pt
    while True:
        font = schrift("Inter-Medium.otf", pt)
        font2 = schrift("Inter-Regular.otf", max(pt * 0.62, 6.5))
        zeilen = umbrechen(zeichnen, titel, font, breite)
        zeilen2 = umbrechen(zeichnen, untertitel, font2, breite) if untertitel else []
        zh, zh2 = round(font.size * 1.15), round(font2.size * 1.3)
        luecke = round(font.size * 0.45) if zeilen2 else 0
        gesamt = len(zeilen) * zh + luecke + len(zeilen2) * zh2
        passt = all(zeichnen.textlength(z, font=font) <= breite for z in zeilen)
        if (passt and gesamt <= hoehe and len(zeilen) <= 4) or pt <= 7:
            break
        pt -= 0.5
    y = y0 + (hoehe - gesamt) // 2
    for z in zeilen:
        zeichnen.text((x0, y), z, font=font, fill=farbe)
        y += zh
    y += luecke
    for z in zeilen2:
        zeichnen.text((x0, y), z, font=font2, fill=farbe2)
        y += zh2


def cover_einsetzen(flaeche: Image.Image, cover: Image.Image, x: int, y: int, kante: int) -> None:
    """Quadratisch zugeschnittenes Cover mit weichem Schatten."""
    quadrat = ImageOps.fit(cover.convert("RGB"), (kante, kante), Image.Resampling.LANCZOS)
    schatten = Image.new("L", flaeche.size, 0)
    versatz, weich = px(0.4), px(0.8)
    ImageDraw.Draw(schatten).rectangle(
        (x + versatz, y + versatz * 2, x + kante + versatz, y + kante + versatz * 2), fill=90
    )
    schatten = schatten.filter(ImageFilter.GaussianBlur(weich))
    flaeche.paste(Image.new("RGB", flaeche.size, (0, 0, 0)), (0, 0), schatten)
    maske = Image.new("L", (kante, kante), 0)
    ImageDraw.Draw(maske).rounded_rectangle((0, 0, kante - 1, kante - 1), px(1.0), fill=255)
    flaeche.paste(quadrat, (x, y), maske)


def etikett(
    cover: Image.Image, titel: str, untertitel: str, bogen: Bogen, layout: str
) -> Image.Image:
    """Ein Etikett samt seitlicher Beschnittzugabe, im Bogen-Querformat."""
    b, h, bx = px(bogen.breite), px(bogen.hoehe), px(BESCHNITT_X)
    farbe = grundfarbe(cover)
    text, text2 = textfarben(farbe)
    innen = px(3.2)

    if layout == "hoch":
        # gedreht gestaltet: Cover oben, Text unten, danach 90° ins Querformat
        flaeche = Image.new("RGB", (h, b + 2 * bx), farbe)
        kante = h - 2 * innen
        cover_einsetzen(flaeche, cover, innen, bx + innen, kante)
        textblock(
            ImageDraw.Draw(flaeche),
            (innen + px(0.5), bx + kante + innen + px(1.5), h - innen, b + bx - innen),
            titel, untertitel, (text, text2), max_pt=13,
        )  # fmt: skip
        return flaeche.rotate(90, expand=True)

    flaeche = Image.new("RGB", (b + 2 * bx, h), farbe)
    kante = h - 2 * innen
    cover_einsetzen(flaeche, cover, bx + innen, innen, kante)
    links = bx + innen + kante + px(3.5)
    textblock(
        ImageDraw.Draw(flaeche),
        (links, innen + px(1), b + bx - innen, h - innen - px(1)),
        titel, untertitel, (text, text2), max_pt=12,
    )  # fmt: skip
    return flaeche


def positionen(bogen: Bogen, dx: float, dy: float) -> list[tuple[int, int]]:
    """Linke obere Ecke jedes Etiketts in px, zeilenweise."""
    return [
        (
            px(bogen.rand_links + dx + s * bogen.schritt_x),
            px(bogen.rand_oben + dy + z * bogen.schritt_y),
        )
        for z in range(bogen.zeilen)
        for s in range(bogen.spalten)
    ]


def bogen_setzen(
    karten: list[Image.Image], bogen: Bogen, start: int, dx: float, dy: float
) -> list[Image.Image]:
    plaetze = positionen(bogen, dx, dy)
    seiten: list[Image.Image] = []
    platz = start - 1
    for karte in karten:
        if not seiten or platz >= len(plaetze):
            seiten.append(Image.new("RGB", A4, "white"))
            platz = platz if len(seiten) == 1 else 0
        x, y = plaetze[platz]
        seiten[-1].paste(karte, (x - px(BESCHNITT_X), y))
        platz += 1
    return seiten


def probe(bogen: Bogen, dx: float, dy: float) -> Image.Image:
    """Umrisse aller Etiketten mit Mittelkreuz, zum Ausrichten auf Normalpapier."""
    seite = Image.new("RGB", A4, "white")
    zeichnen = ImageDraw.Draw(seite)
    font = schrift("Inter-Regular.otf", 8)
    b, h = px(bogen.breite), px(bogen.hoehe)
    for i, (x, y) in enumerate(positionen(bogen, dx, dy), 1):
        zeichnen.rounded_rectangle((x, y, x + b, y + h), px(bogen.radius), outline="black", width=2)
        mx, my = x + b // 2, y + h // 2
        zeichnen.line((mx - px(3), my, mx + px(3), my), fill="black", width=2)
        zeichnen.line((mx, my - px(3), mx, my + px(3)), fill="black", width=2)
        zeichnen.text((x + px(3), y + px(2.5)), str(i), font=font, fill="black")
    hinweis = f"Probe HERMA 5028 · Versatz x {dx:+.1f} mm, y {dy:+.1f} mm · 100 % drucken"
    zeichnen.text((px(18.6), px(10)), hinweis, font=font, fill="black")
    return seite


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("ordner", nargs="?", type=Path, help="Ordner mit Covern")
    p.add_argument("-o", "--ausgabe", type=Path, default=Path("karten.pdf"))
    p.add_argument("--layout", choices=("quer", "hoch"), default="quer")
    p.add_argument("--start", type=int, default=1, help="erstes freies Etikett (1-10)")
    p.add_argument("--kopien", type=int, default=1, help="Etiketten pro Cover")
    p.add_argument("--versatz-x", type=float, default=0.0, help="mm, + = nach rechts")
    p.add_argument("--versatz-y", type=float, default=0.0, help="mm, + = nach unten")
    p.add_argument("--probe", action="store_true", help="nur Umrisse zum Ausrichten")
    p.add_argument("--png", action="store_true", help="zusätzlich PNG-Vorschau je Seite")
    a = p.parse_args()
    bogen = HERMA_5028

    if a.probe:
        seiten = [probe(bogen, a.versatz_x, a.versatz_y)]
    else:
        if a.ordner is None or not a.ordner.is_dir():
            p.error("ORDNER mit Covern angeben (oder --probe)")
        if not 1 <= a.start <= bogen.spalten * bogen.zeilen:
            p.error("--start muss zwischen 1 und 10 liegen")
        dateien = sorted(f for f in a.ordner.iterdir() if f.suffix.lower() in BILDER)
        if not dateien:
            p.error(f"keine Bilder in {a.ordner}")
        karten = []
        for datei in dateien:
            with Image.open(datei) as cover:
                karte = etikett(cover, *titel_aus_datei(datei), bogen, a.layout)
            karten += [karte] * a.kopien
        seiten = bogen_setzen(karten, bogen, a.start, a.versatz_x, a.versatz_y)

    seiten[0].save(a.ausgabe, save_all=True, append_images=seiten[1:], resolution=DPI)
    if a.png:
        for i, seite in enumerate(seiten, 1):
            seite.save(a.ausgabe.with_name(f"{a.ausgabe.stem}-{i}.png"))
    print(f"{a.ausgabe}: {len(seiten)} Seite(n)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
