# NFC-Musikbox für Home Assistant

Karte auflegen = Musik, Karte abziehen = Pause. Die Integration verbindet einen
ESPHome-NFC-Leser (PN532, zwei Tasten) mit einem Lautsprecher und merkt sich pro
Karte, wo die Wiedergabe stand – wie bei einer Tonie-Box.

> **Status:** in Entwicklung (Meilenstein 1: Gerüst und Einrichtung). Wiedergabe-Logik,
> Tasten, LED und die Oberfläche folgen.

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
