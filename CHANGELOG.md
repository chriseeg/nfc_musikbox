# Changelog

Format nach [Keep a Changelog](https://keepachangelog.com/de/1.1.0/).

## [Unreleased]

## [0.3.0]

### Hinzugefügt
- M3: Tasten Play/Pause, Zurück kurz (30 s) und lang (von vorne), nur mit aufgelegter
  Karte. Ersetzt das Lesegerät-Blueprint.
- LED-Status an `esphome.<node>_set_playback_state`: bei Zustandsänderung des Players,
  HA-Start und `esphome.nfc_reader_online` (nur für das eigene Lesegerät).

### Geändert gegenüber dem Blueprint
- Tastendruck wird am Zeitstempel des Events erkannt: Der erste Druck nach HA-Start
  zählt, alte Zeitstempel nach einem Reconnect lösen nichts aus.
- LED wird nur bei Zustandsänderung gesendet, nicht bei jeder Positionsänderung.
- Ein Tastendruck bricht ein laufendes Fortsetzen ab.

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
