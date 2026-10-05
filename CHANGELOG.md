# Changelog

Format nach [Keep a Changelog](https://keepachangelog.com/de/1.1.0/).

## [Unreleased]

## [0.2.0]

### Hinzugefügt
- M2: Zustandsmaschine pro Lesegerät (Tonie und Einfach), ersetzt das Karten-Blueprint.
  Ein neues Ereignis bricht einen laufenden Ablauf ab.
- Sonos-Backend: Fortsetzen per `sonos.play_queue` (0-basiert) und `media_seek` mit
  Wiederholungen; andere Player starten von vorne.
- Rücksprung beim Entfernen gleicht die 2-s-Entprellung der Firmware aus.
- Dienste `assign_card`, `remove_card`, `reset_position`.

### Geändert gegenüber dem Blueprint
- Kartenwechsel läuft sequenziell (erst alte Karte speichern, dann neue starten)
  statt über zwei parallele Automationen.
- Wird eine Karte abgezogen, während ihr Fortsetzen noch läuft, bleibt die gemerkte
  Stelle erhalten (das Blueprint überschrieb sie mit der Startposition).

## [0.1.0]

### Hinzugefügt
- M1: Gerüst der Integration `nfc_musikbox` (ein Config Entry).
- Lesegeräte als Subentries („Lesegerät hinzufügen“): ESPHome-Gerät + Lautsprecher,
  Lautsprecher nachträglich änderbar.
- Automatisches Finden von Sensor „Karte auf dem Reader“, Tasten-Events und der
  ESPHome-LED-Aktion pro Lesegerät.
- Diagnose-Sensor „Aktuelle Karte“ pro Lesegerät.
- Optionen für Zeiten und Toleranzen (Kartenwechsel, Rücksprung, Sonos-Fortsetzen).
- Speicher für Karten und Positionen (`.storage/nfc_musikbox`).
- Diagnose-Download, CI (ruff, mypy, pytest, hassfest, HACS).
