"""Konstanten der NFC-Musikbox."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "nfc_musikbox"

SUBENTRY_READER: Final = "reader"

CONF_DEVICE_ID: Final = "device_id"
CONF_PLAYER: Final = "media_player"

# Zustand des Sensors "Karte auf dem Reader", wenn keine Karte liegt
NO_CARD: Final = "none"

# Entitäten des Readers werden über Suffix der Entity-ID oder den Originalnamen
# gefunden, weil der Bereichs-Präfix (z. B. "kinderzimmer_") variieren kann.
CARD_SENSOR_SUFFIX: Final = "karte_auf_dem_reader"
CARD_SENSOR_NAME: Final = "Karte auf dem Reader"
PLAY_EVENT_SUFFIX: Final = "ereignis_taste_play_pause"
PLAY_EVENT_NAME: Final = "Ereignis Taste Play/Pause"
BACK_EVENT_SUFFIX: Final = "ereignis_taste_zuruck"
BACK_EVENT_NAME: Final = "Ereignis Taste Zurück"

LED_ACTION_SUFFIX: Final = "set_playback_state"
EVENT_READER_ONLINE: Final = "esphome.nfc_reader_online"

# Optionen (Options Flow) mit Standardwerten
OPT_CARD_CHANGE_DELAY: Final = "card_change_delay"
OPT_REMOVAL_REWIND: Final = "removal_rewind"
OPT_RESTORE_SETTLE: Final = "restore_settle"
OPT_RESTORE_START_TIMEOUT: Final = "restore_start_timeout"
OPT_QUEUE_JUMP_TIMEOUT: Final = "queue_jump_timeout"
OPT_SEEK_TOLERANCE: Final = "seek_tolerance"
OPT_SEEK_ATTEMPTS: Final = "seek_attempts"
OPT_SEEK_RETRY_DELAY: Final = "seek_retry_delay"
OPT_SKIP_BACK: Final = "skip_back"

DEFAULT_OPTIONS: Final[dict[str, float]] = {
    OPT_CARD_CHANGE_DELAY: 0.6,
    OPT_REMOVAL_REWIND: 2.0,
    OPT_RESTORE_SETTLE: 1.0,
    OPT_RESTORE_START_TIMEOUT: 20.0,
    OPT_QUEUE_JUMP_TIMEOUT: 10.0,
    OPT_SEEK_TOLERANCE: 10.0,
    OPT_SEEK_ATTEMPTS: 3,
    OPT_SEEK_RETRY_DELAY: 2.0,
    OPT_SKIP_BACK: 30.0,
}

STORAGE_KEY: Final = DOMAIN
STORAGE_VERSION: Final = 1
