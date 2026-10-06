# NFC-Musikbox für Home Assistant

Karte auflegen = Musik, Karte abziehen = Pause. Die Integration verbindet einen
ESPHome-NFC-Leser (PN532, zwei Tasten) mit einem Lautsprecher und merkt sich pro
Karte, wo die Wiedergabe stand – wie bei einer Tonie-Box.

## Voraussetzungen

- Home Assistant **2026.9** oder neuer
- Lesegerät mit der ESPHome-Firmware aus [`reference/esphome-nfc-musikbox-v3.yaml`](reference/esphome-nfc-musikbox-v3.yaml)
- Für das Fortsetzen an der gemerkten Stelle: ein **Sonos**-Lautsprecher
  (andere Player starten das Medium von vorne)

## Installation (HACS)

1. HACS → ⋮ (oben rechts) → *Benutzerdefinierte Repositories* →
   `https://github.com/chriseeg/nfc_musikbox`, Typ *Integration*.
2. „NFC-Musikbox“ suchen → *Herunterladen* (neueste Version) → Home Assistant neu starten.
3. *Einstellungen → Geräte & Dienste → Integration hinzufügen → NFC-Musikbox*.
4. In der Integration **„Lesegerät hinzufügen“**: ESPHome-Gerät und Lautsprecher wählen.

Pro Lesegerät entsteht ein Gerät mit dem Diagnose-Sensor **„Aktuelle Karte“**. Seine
Attribute zeigen, welche Entitäten gefunden wurden (Karten-Sensor, Tasten, LED-Aktion).

## Verhalten

**Hörspiel-Modus** (Standard, intern `tonie`)

| Aktion | Ergebnis |
|---|---|
| Karte auflegen | Medium startet und setzt an der gemerkten Stelle fort |
| Karte abziehen | Stelle wird gemerkt, Wiedergabe pausiert |
| Karte A gegen B tauschen | A merkt sich die Stelle (ohne Pause), kurz danach startet B |
| Karte wieder auflegen, Player noch pausiert auf gleichem Titel | einfach weiter |

Gemerkt werden Titelnummer in der Queue, Sekunde und Titelname. Fortsetzen an der Stelle
klappt nur mit Sonos (Titelsprung per `sonos.play_queue`, dann Spulen). Andere Player
starten das Medium von vorne.

**Musik-Modus** (intern `simple`): Auflegen startet das Medium von vorne, Abziehen tut
nichts. Pro Karte einstellbar: **Zufallswiedergabe** und **Wiederholen** (aus/alle/Titel)
oder „nicht ändern“.

Der Hörspiel-Modus schaltet die Zufallswiedergabe beim Start immer aus: Sonos merkt sich
Shuffle pro Lautsprecher, und mit Shuffle würde die gemerkte Titelnummer nicht passen.

**Startlautstärke** pro Lesegerät (Panel → Lesegeräte oder Entität „Startlautstärke“ am
Lesegerät): Beim Auflegen einer Karte wird vor dem Abspielen diese Lautstärke gesetzt.
0 % = Lautstärke nicht ändern.

Karten ohne Zuordnung, deaktivierte Karten und Karten, die für ein anderes Lesegerät
freigegeben sind, werden ignoriert. Zustandswechsel von/nach `unavailable`/`unknown`
(Reconnect, HA-Start) lösen nichts aus.

## Tasten und LED

Tasten wirken nur, solange eine Karte aufliegt.

| Taste | Wirkung |
|---|---|
| Play/Pause | Wiedergabe/Pause |
| Zurück kurz | 30 s zurück (einstellbar) |
| Zurück lang (≥ 0,8 s) | von vorne: Sonos an den Anfang der Queue, andere Player an den Anfang des Titels |

Ein Tastendruck während des Fortsetzens bricht das Fortsetzen ab.

Die LED zeigt den Zustand des Lautsprechers (Play-LED an = spielt, pulsiert = Pause).
Home Assistant sendet ihn bei jeder Zustandsänderung, beim Start und wenn sich das
Lesegerät neu verbindet (`esphome.nfc_reader_online`).

## Oberfläche „Musikkarten“

In der Seitenleiste erscheint **Musikkarten** (nur für Administratoren):

- **Noch ohne Musik**: neu gescannte Karten (eine aufliegende Karte ist hervorgehoben) →
  *Zuordnen*. Das ✕ entfernt die Karte aus der Liste und aus den Tags von Home Assistant.
- **Zugeordnete Karten**: Cover, Medium, Lautsprecher, letzter Scan, gemerkte Stelle,
  ▶ spielt die Karte zum Testen von vorne ab.
- **Karte bearbeiten**: Name (wird auch in die Tag-Verwaltung übernommen), Medium über den
  Medien-Browser, Betriebsart, gemerkte Stelle zurücksetzen, Lesegeräte, aktiv/inaktiv,
  Test „Von vorne“ / „Fortsetzen“, Zuordnung löschen.
- **Lesegeräte** (Zahnrad): Status und Lautsprecher je Lesegerät; Hinzufügen und Ändern
  über die Integrationsseite.

Pro Karte entsteht außerdem ein Gerät mit den Entitäten **Aktiv** (Schalter),
**Betriebsart** (Auswahl) und **Gemerkte Stelle** (Sensor), z. B. für Automationen oder
ein Dashboard.

## Karten per Dienst zuordnen

```yaml
action: nfc_musikbox.assign_card
data:
  tag_id: CA-09-0C-05
  name: Hörspiel Puderzucker
  media:
    entity_id: media_player.sonos_kinderzimmer
    media_content_id: FV:2/46
    media_content_type: favorite_item_id
  mode: tonie
```

Außerdem: `nfc_musikbox.remove_card`, `nfc_musikbox.reset_position`,
`nfc_musikbox.play_card` (Test-Wiedergabe, optional mit Fortsetzen).

## Optionen

| Option | Standard | Bedeutung |
|---|---|---|
| Pause beim Kartenwechsel | 0,6 s | Zwischen Speichern der alten und Start der neuen Karte |
| Rücksprung beim Entfernen | 2 s | Gleicht die 2-s-Entprellung der Firmware aus |
| Zurück-Taste (kurz) | 30 s | Sprungweite |
| Wartezeit nach Start des Mediums | 1 s | Vor dem Titelsprung (Sonos) |
| Timeout bis Wiedergabe startet | 20 s | Danach wird das Fortsetzen abgebrochen |
| Timeout für Titelsprung | 10 s | Warten auf die richtige Queue-Position |
| Toleranz beim Spulen | 10 s | Ab hier gilt die Stelle als erreicht |
| Spulversuche / Pause | 3 / 2 s | Wiederholungen von `media_seek` |

## Umstieg von den Blueprints

Wer vorher die Blueprints `nfc_musikbox_karte_tonie` / `nfc_musikbox_lesegeraet` und die
Custom Card „NFC-Karten-Manager“ genutzt hat:

1. Integration einrichten, Karten im Panel neu zuordnen und testen.
2. Danach entfernen (sonst reagieren alte und neue Logik doppelt):
   - Automationen aus den Blueprints (pro Karte und pro Lesegerät)
   - Helfer `input_text.nfc_position_*` und den Sperr-Timer
   - die Blueprints selbst
   - Dashboard-Ressource `/local/nfc-karten/nfc-karten.js` und das Dashboard der Card
3. Die Firmware des Lesegeräts bleibt unverändert.

Gemerkte Positionen aus den `input_text`-Helfern werden nicht übernommen; beim nächsten
Abziehen der Karte wird die Stelle neu gemerkt.

## Bekannte Einschränkungen

- **Fortsetzen an der Stelle** gibt es nur für Sonos; andere Player starten von vorne.
- Gemerkt wird **pro Karte**, nicht pro Lesegerät: Liegt dieselbe Karte an einem zweiten
  Lesegerät, setzt sie dort an der zuletzt gemerkten Stelle fort.
- Das Panel ist nur für **Administratoren** sichtbar. Die Bedienung per Karte und Tasten
  braucht kein Konto.

## Release erstellen

`version` in `manifest.json` und einen Abschnitt `## [X.Y.Z]` in `CHANGELOG.md` pflegen,
mergen, dann Tag `vX.Y.Z` auf `main` pushen. Der Workflow „Release“ erzeugt das
GitHub-Release; HACS bietet es danach als Update an.

## Debugging

```yaml
logger:
  logs:
    custom_components.nfc_musikbox: debug
```

Diagnose: *Einstellungen → Geräte & Dienste → NFC-Musikbox → ⋮ → Diagnose herunterladen*.

## Lizenz

MIT, siehe [LICENSE](LICENSE).
