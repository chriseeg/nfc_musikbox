# NFC-Musikbox für Home Assistant

Karte auflegen = Musik, Karte abziehen = Pause. Die Integration verbindet einen
ESPHome-NFC-Leser (PN532, zwei Tasten) mit einem Lautsprecher und merkt sich pro
Karte, wo die Wiedergabe stand – wie bei einer Tonie-Box.

> **Status:** in Entwicklung. Fertig: Einrichtung, Wiedergabe-Logik (Tonie/Einfach) und
> Dienste zum Zuordnen. Es folgen Tasten und LED (M3) und die Oberfläche (M4).

## Voraussetzungen

- Home Assistant **2026.9** oder neuer
- Lesegerät mit der ESPHome-Firmware aus [`reference/esphome-nfc-musikbox-v3.yaml`](reference/esphome-nfc-musikbox-v3.yaml)
- Für das Fortsetzen an der gemerkten Stelle: ein **Sonos**-Lautsprecher
  (andere Player starten das Medium von vorne)

## Installation (HACS)

1. HACS → Integrationen → ⋮ → *Benutzerdefinierte Repositories* →
   `https://github.com/chriseeg/nfc_musikbox`, Kategorie *Integration*.
2. „NFC-Musikbox“ installieren, Home Assistant neu starten.
3. *Einstellungen → Geräte & Dienste → Integration hinzufügen → NFC-Musikbox*.
4. In der Integration **„Lesegerät hinzufügen“**: ESPHome-Gerät und Lautsprecher wählen.

Pro Lesegerät entsteht ein Gerät mit dem Diagnose-Sensor **„Aktuelle Karte“**. Seine
Attribute zeigen, welche Entitäten gefunden wurden (Karten-Sensor, Tasten, LED-Aktion).

## Verhalten

**Tonie** (Standard)

| Aktion | Ergebnis |
|---|---|
| Karte auflegen | Medium startet und setzt an der gemerkten Stelle fort |
| Karte abziehen | Stelle wird gemerkt, Wiedergabe pausiert |
| Karte A gegen B tauschen | A merkt sich die Stelle (ohne Pause), kurz danach startet B |
| Karte wieder auflegen, Player noch pausiert auf gleichem Titel | einfach weiter |

Gemerkt werden Titelnummer in der Queue, Sekunde und Titelname. Fortsetzen an der Stelle
klappt nur mit Sonos (Titelsprung per `sonos.play_queue`, dann Spulen). Andere Player
starten das Medium von vorne.

**Einfach**: Auflegen startet das Medium von vorne, Abziehen tut nichts.

Karten ohne Zuordnung, deaktivierte Karten und Karten, die für ein anderes Lesegerät
freigegeben sind, werden ignoriert. Zustandswechsel von/nach `unavailable`/`unknown`
(Reconnect, HA-Start) lösen nichts aus.

## Karten zuordnen (bis die Oberfläche fertig ist)

*Entwicklerwerkzeuge → Aktionen → „NFC-Musikbox: Karte zuordnen“* (`nfc_musikbox.assign_card`):
Tag-ID (steht im Sensor „Aktuelle Karte“, wenn die Karte aufliegt), Medium über den
Medien-Browser, Betriebsart, optional Lesegeräte.

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

Außerdem: `nfc_musikbox.remove_card`, `nfc_musikbox.reset_position`.

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

## Bekannte Einschränkungen

- Playlisten mit **Zufallswiedergabe**: Die Queue-Position ist nicht stabil, Fortsetzen
  trifft dann nicht den richtigen Titel.

## Debugging

```yaml
logger:
  logs:
    custom_components.nfc_musikbox: debug
```

Diagnose: *Einstellungen → Geräte & Dienste → NFC-Musikbox → ⋮ → Diagnose herunterladen*.

## Lizenz

MIT, siehe [LICENSE](LICENSE).
