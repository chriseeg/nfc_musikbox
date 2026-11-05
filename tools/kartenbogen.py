#!/usr/bin/env python3
"""Setzt Album-Cover als Etiketten für die Musikkarten (HERMA 5028, 83,8 x 50,8 mm).

  kartenbogen.py ORDNER [-o karten.pdf] [--layout quer|hoch] [--start N] [--kopien N]
  kartenbogen.py --probe [-o probe.pdf]

ORDNER enthält die Cover (JPEG, PNG, WebP). Der Dateiname ist der Text auf dem Etikett:
„Puderzucker - Folge 3.jpg“ wird zu Titel „Puderzucker“ und Untertitel „Folge 3“. Eine
Nummer vorne („01 Puderzucker.jpg“) legt nur die Reihenfolge fest und wird nicht gedruckt.

Die Grundfarbe jedes Etiketts kommt aus dem Cover, die Schrift wird hell oder dunkel, je
nachdem, was darauf besser lesbar ist.

Im PDF sind Cover und Hintergrund Pixelbilder (300 dpi), jeder Text ist dagegen als
Vektorkontur gezeichnet: scharf in jeder Vergrößerung, keine Schrift eingebettet nötig.
Abhängigkeiten: pillow, reportlab, fonttools (für --png zusätzlich pypdfium2).

Drucken: „Tatsächliche Größe“ bzw. 100 %, nicht „An Seite anpassen“. Vorher einmal
--probe auf Normalpapier drucken, gegen das Licht auf einen Etikettenbogen legen und den
Versatz mit --versatz-x/--versatz-y (mm, positiv = nach rechts/unten) ausgleichen.
"""

import argparse
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from functools import cache
from pathlib import Path
import re
import sys

from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFilter, ImageOps
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

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


class Schrift:
    """Metriken und Umrisse einer OTF/TTF-Datei (ohne Kerning)."""

    def __init__(self, datei: str) -> None:
        font = TTFont(SCHRIFTEN / datei)
        self.einheiten: int = font["head"].unitsPerEm
        self.ascent: int = font["hhea"].ascent
        self.cmap = font.getBestCmap()
        self.hmtx = font["hmtx"]
        self.glyphen = font.getGlyphSet()

    def glyph(self, zeichen: str) -> str:
        return self.cmap.get(ord(zeichen), ".notdef")

    def breite(self, text: str, groesse: float) -> float:
        return sum(self.hmtx[self.glyph(z)][0] for z in text) * groesse / self.einheiten


@cache
def schrift(name: str) -> Schrift:
    return Schrift(name)


def pt_zu_px(pt: float) -> float:
    return pt * DPI / 72


@dataclass(frozen=True)
class Text:
    """Eine Textzeile; x/y = Beginn der Grundlinie in px, drehung 0 oder 90 (gegen Uhrzeiger)."""

    text: str
    schrift: str
    groesse: float
    farbe: tuple[int, int, int]
    alpha: int
    x: float
    y: float
    drehung: int = 0


def text_oben(  # noqa: PLR0917
    x: float, y_oben: float, text: str, name: str, groesse: float,
    farbe: tuple[int, int, int], alpha: int = 255,
) -> Text:  # fmt: skip
    """Text, dessen Zeilenkopf (Ascender) bei y_oben liegt."""
    f = schrift(name)
    return Text(text, name, groesse, farbe, alpha, x, y_oben + f.ascent * groesse / f.einheiten)


@dataclass
class Seite:
    bild: Image.Image | None = None
    texte: list[Text] = field(default_factory=list)
    zeichnen: Callable[[canvas.Canvas, float], None] | None = None


class _PdfStift(BasePen):
    def __init__(self, glyphen, pfad, abbilden) -> None:
        super().__init__(glyphen)
        self.pfad, self.abb = pfad, abbilden

    def _moveTo(self, p):
        self.pfad.moveTo(*self.abb(p))

    def _lineTo(self, p):
        self.pfad.lineTo(*self.abb(p))

    def _curveToOne(self, a, b, c):
        self.pfad.curveTo(*self.abb(a), *self.abb(b), *self.abb(c))

    def _closePath(self):
        self.pfad.close()


def text_vektor(c: canvas.Canvas, t: Text, seite_hoehe_pt: float) -> None:
    """Zeichnet die Glyphenumrisse als gefüllte Pfade (kein Font im PDF)."""
    f = schrift(t.schrift)
    faktor = t.groesse / f.einheiten
    pfad = c.beginPath()
    vorschub = 0.0
    for zeichen in t.text:
        name = f.glyph(zeichen)

        def abb(p, v=vorschub):
            u, w = v + p[0] * faktor, -p[1] * faktor  # u entlang der Schrift, w nach unten
            x, y = (t.x + u, t.y + w) if t.drehung == 0 else (t.x + w, t.y - u)
            return x * 72 / DPI, seite_hoehe_pt - y * 72 / DPI

        f.glyphen[name].draw(_PdfStift(f.glyphen, pfad, abb))
        vorschub += f.hmtx[name][0] * faktor
    c.setFillColorRGB(*(v / 255 for v in t.farbe), alpha=t.alpha / 255)
    c.drawPath(pfad, stroke=0, fill=1, fillMode=1)  # 1 = nonzero, wie CFF/TrueType


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
        return (43, 37, 32, 255), (43, 37, 32, 170)
    return (255, 252, 246, 255), (255, 252, 246, 190)


def umbrechen(text: str, name: str, groesse: float, breite: float) -> list[str]:
    f = schrift(name)
    zeilen: list[str] = []
    for wort in text.split():
        if zeilen and f.breite(f"{zeilen[-1]} {wort}", groesse) <= breite:
            zeilen[-1] += f" {wort}"
        else:
            zeilen.append(wort)
    return zeilen


def textblock(
    box: tuple[int, int, int, int],
    titel: str,
    untertitel: str,
    farben: tuple[tuple[int, ...], tuple[int, ...]],
    *,
    max_pt: float,
) -> list[Text]:
    """Titel und Untertitel, linksbündig, vertikal mittig; Titel schrumpft bis er passt."""
    x0, y0, x1, y1 = box
    farbe, farbe2 = farben
    breite, hoehe = x1 - x0, y1 - y0
    pt = max_pt
    while True:
        gr = round(pt_zu_px(pt))
        gr2 = round(pt_zu_px(max(pt * 0.62, 6.5)))
        zeilen = umbrechen(titel, "Inter-Medium.otf", gr, breite)
        zeilen2 = umbrechen(untertitel, "Inter-Regular.otf", gr2, breite) if untertitel else []
        zh, zh2 = round(gr * 1.15), round(gr2 * 1.3)
        luecke = round(gr * 0.45) if zeilen2 else 0
        gesamt = len(zeilen) * zh + luecke + len(zeilen2) * zh2
        passt = all(schrift("Inter-Medium.otf").breite(z, gr) <= breite for z in zeilen)
        if (passt and gesamt <= hoehe and len(zeilen) <= 4) or pt <= 7:
            break
        pt -= 0.5
    texte: list[Text] = []
    y = y0 + (hoehe - gesamt) // 2
    for z in zeilen:
        texte.append(text_oben(x0, y, z, "Inter-Medium.otf", gr, farbe[:3], farbe[3]))
        y += zh
    y += luecke
    for z in zeilen2:
        texte.append(text_oben(x0, y, z, "Inter-Regular.otf", gr2, farbe2[:3], farbe2[3]))
        y += zh2
    return texte


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
) -> tuple[Image.Image, list[Text]]:
    """Ein Etikett samt seitlicher Beschnittzugabe, im Bogen-Querformat; Text getrennt."""
    b, h, bx = px(bogen.breite), px(bogen.hoehe), px(BESCHNITT_X)
    farbe = grundfarbe(cover)
    farben = textfarben(farbe)
    innen = px(3.2)

    if layout == "hoch":
        # gedreht gestaltet: Cover oben, Text unten, danach 90° ins Querformat
        flaeche = Image.new("RGB", (h, b + 2 * bx), farbe)
        kante = h - 2 * innen
        cover_einsetzen(flaeche, cover, innen, bx + innen, kante)
        texte = textblock(
            (innen + px(0.5), bx + kante + innen + px(1.5), h - innen, b + bx - innen),
            titel, untertitel, farben, max_pt=13,
        )  # fmt: skip
        # Punkt (x, y) landet nach rotate(90, expand) bei (y, Breite - x)
        gedreht = [replace(t, x=t.y, y=h - t.x, drehung=90) for t in texte]
        return flaeche.rotate(90, expand=True), gedreht

    flaeche = Image.new("RGB", (b + 2 * bx, h), farbe)
    kante = h - 2 * innen
    cover_einsetzen(flaeche, cover, bx + innen, innen, kante)
    links = bx + innen + kante + px(3.5)
    texte = textblock(
        (links, innen + px(1), b + bx - innen, h - innen - px(1)),
        titel, untertitel, farben, max_pt=12,
    )  # fmt: skip
    return flaeche, texte


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
    karten: list[tuple[Image.Image, list[Text]]], bogen: Bogen, start: int, dx: float, dy: float
) -> list[Seite]:
    plaetze = positionen(bogen, dx, dy)
    seiten: list[Seite] = []
    platz = start - 1
    for bild, texte in karten:
        if not seiten or platz >= len(plaetze):
            seiten.append(Seite(Image.new("RGB", A4, "white")))
            platz = platz if len(seiten) == 1 else 0
        x, y = plaetze[platz]
        x -= px(BESCHNITT_X)
        assert seiten[-1].bild is not None
        seiten[-1].bild.paste(bild, (x, y))
        seiten[-1].texte += [replace(t, x=t.x + x, y=t.y + y) for t in texte]
        platz += 1
    return seiten


def probe(bogen: Bogen, dx: float, dy: float) -> Seite:
    """Umrisse aller Etiketten mit Mittelkreuz, zum Ausrichten auf Normalpapier (rein vektor)."""
    b, h = px(bogen.breite), px(bogen.hoehe)
    plaetze = positionen(bogen, dx, dy)
    schwarz = (0, 0, 0)
    gr = round(pt_zu_px(8))
    texte = [
        text_oben(x + px(3), y + px(2.5), str(i), "Inter-Regular.otf", gr, schwarz)
        for i, (x, y) in enumerate(plaetze, 1)
    ]
    hinweis = f"Probe HERMA 5028 · Versatz x {dx:+.1f} mm, y {dy:+.1f} mm · 100 % drucken"
    texte.append(text_oben(px(18.6), px(10), hinweis, "Inter-Regular.otf", gr, schwarz))

    def zeichnen(c: canvas.Canvas, hoehe_pt: float) -> None:
        k = 72 / DPI
        c.setStrokeColorRGB(0, 0, 0)
        c.setLineWidth(2 * k)
        for x, y in plaetze:
            c.roundRect(x * k, hoehe_pt - (y + h) * k, b * k, h * k, px(bogen.radius) * k, 1, 0)
            mx, my = (x + b / 2) * k, hoehe_pt - (y + h / 2) * k
            c.line(mx - px(3) * k, my, mx + px(3) * k, my)
            c.line(mx, my - px(3) * k, mx, my + px(3) * k)

    return Seite(None, texte, zeichnen)


def pdf_schreiben(seiten: list[Seite], pfad: Path) -> None:
    breite_pt, hoehe_pt = A4[0] * 72 / DPI, A4[1] * 72 / DPI
    c = canvas.Canvas(str(pfad), pagesize=(breite_pt, hoehe_pt))
    c.setTitle("Musikkarten-Etiketten")
    for seite in seiten:
        if seite.bild is not None:
            c.drawImage(ImageReader(seite.bild), 0, 0, breite_pt, hoehe_pt)
        if seite.zeichnen:
            seite.zeichnen(c, hoehe_pt)
        for t in seite.texte:
            text_vektor(c, t, hoehe_pt)
        c.showPage()
    c.save()


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

    pdf_schreiben(seiten, a.ausgabe)
    if a.png:
        import pypdfium2  # nur für die Vorschau nötig  # noqa: PLC0415

        pdf = pypdfium2.PdfDocument(str(a.ausgabe))
        for i, seite in enumerate(pdf, 1):
            vorschau = seite.render(scale=DPI / 72).to_pil()
            vorschau.save(a.ausgabe.with_name(f"{a.ausgabe.stem}-{i}.png"))
    print(f"{a.ausgabe}: {len(seiten)} Seite(n)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
