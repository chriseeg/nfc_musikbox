# Changelog

Format nach [Keep a Changelog](https://keepachangelog.com/de/1.1.0/).

## [Unreleased]

## [0.6.1]

### Behoben
- Fehler „Task was destroyed but it is pending“ (LED-Status) beim Herunterfahren von Home
  Assistant: Während HA stoppt, wird kein LED-Status mehr gesendet; offene Aufträge werden
  beim Entladen abgebrochen.

## [0.6.0]

### Geändert
- Betriebsarten heißen jetzt **Hörspiel-Modus** (vorher „Tonie“) und **Musik-Modus**
  (vorher „Einfach“). Intern bleiben die Werte `tonie`/`simple`: keine Datenmigration,
  Automationen auf die Betriebsart-Entität funktionieren weiter.
- Hörspiel-Modus schaltet die Zufallswiedergabe beim Start aus (Sonos merkt sich Shuffle
  pro Lautsprecher; mit Shuffle stimmt die gemerkte Titelnummer nicht).

### Hinzugefügt
- Musik-Modus: pro Karte **Zufallswiedergabe** (an/aus/unverändert) und **Wiederholen**
  (aus/alle/Titel/unverändert), gesetzt vor dem Start.
- **Startlautstärke** pro Lesegerät (Panel → Lesegeräte, Entität `number` „Startlautstärke“):
  wird beim Auflegen vor dem Abspielen gesetzt, 0 % = nicht ändern.
- Websocket-Befehl `nfc_musikbox/reader/update`; `assign_card` kennt `shuffle`/`repeat`.

## [0.5.0]

Erstes Release für HACS. Ersetzt die Blueprints „NFC-Musikbox – Karte“ und
„NFC-Musikbox – Lesegerät“ sowie die Custom Card „NFC-Karten-Manager“.

### Hinzugefügt
- Release-Workflow: Tag `vX.Y.Z` erzeugt ein GitHub-Release (prüft die Manifest-Version).
- README: Umstieg von den Blueprints, Einschränkungen, Release-Ablauf.
- Gesicherter Altbestand aus Home Assistant unter `reference/ha-altbestand/`.

## [0.4.0]

### Hinzugefügt
- M4: Panel „Musikkarten“ (nur Admins, mobile first, Dark Mode): Noch ohne Musik,
  Zugeordnete Karten, Karte bearbeiten mit Medien-Browser, Lesegeräte-Übersicht.
- Websocket-API `nfc_musikbox/*` (subscribe, card/save, card/delete, card/play,
  position/reset, seen/delete), Live-Updates, übersteht Reloads.
- Pro Karte ein Gerät mit Schalter „Aktiv“, Auswahl „Betriebsart“, Sensor „Gemerkte Stelle“.
- Dienst `play_card` (Test-Wiedergabe, optional mit Fortsetzen).
- Gescannte Karten werden gemerkt (auch ohne Zuordnung) für „Noch ohne Musik“.

### Geändert
- Player-Backend (Sonos/allgemein) wird bei jeder Nutzung bestimmt statt einmalig beim Start.

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
