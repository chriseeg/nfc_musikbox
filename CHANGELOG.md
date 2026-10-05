# Changelog

Format nach [Keep a Changelog](https://keepachangelog.com/de/1.1.0/).

## [Unreleased]

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
