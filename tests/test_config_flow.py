"""Config Flow, Lesegeräte-Subentries und Optionen."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.nfc_musikbox.const import (
    CONF_DEVICE_ID,
    CONF_PLAYER,
    DEFAULT_OPTIONS,
    DOMAIN,
    OPT_SEEK_ATTEMPTS,
    OPT_SKIP_BACK,
    SUBENTRY_READER,
)

from .conftest import PLAYER, make_entry, make_reader


async def test_user_flow_creates_single_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "NFC-Musikbox"

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_add_reader(hass: HomeAssistant) -> None:
    reader = make_reader(hass)
    entry = make_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_READER), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_DEVICE_ID: reader.device_id, CONF_PLAYER: PLAYER}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "NFC Musikbox"
    await hass.async_block_till_done()

    (subentry,) = entry.subentries.values()
    assert subentry.data == {CONF_DEVICE_ID: reader.device_id, CONF_PLAYER: PLAYER}
    # Update-Listener lädt neu, das Lesegerät ist danach aktiv
    assert subentry.subentry_id in entry.runtime_data.readers


async def test_add_reader_without_card_sensor(hass: HomeAssistant) -> None:
    reader = make_reader(hass, with_card_sensor=False)
    entry = make_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_READER), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_DEVICE_ID: reader.device_id, CONF_PLAYER: PLAYER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_DEVICE_ID: "no_card_sensor"}


async def test_add_reader_twice(hass: HomeAssistant) -> None:
    reader = make_reader(hass)
    entry = make_entry(reader)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_READER), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_DEVICE_ID: reader.device_id, CONF_PLAYER: PLAYER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_DEVICE_ID: "already_configured"}


async def test_reconfigure_reader_player(hass: HomeAssistant) -> None:
    reader = make_reader(hass)
    entry = make_entry(reader)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    (subentry,) = entry.subentries.values()

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_READER),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": subentry.subentry_id},
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_PLAYER: "media_player.sonos_wohnzimmer"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()

    assert entry.subentries[subentry.subentry_id].data[CONF_PLAYER] == (
        "media_player.sonos_wohnzimmer"
    )
    assert entry.runtime_data.readers[subentry.subentry_id].player == (
        "media_player.sonos_wohnzimmer"
    )


async def test_options_flow(hass: HomeAssistant) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    user_input = {**DEFAULT_OPTIONS, OPT_SKIP_BACK: 15.0, OPT_SEEK_ATTEMPTS: 5.0}
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert entry.options[OPT_SEEK_ATTEMPTS] == 5
    assert isinstance(entry.options[OPT_SEEK_ATTEMPTS], int)
    assert entry.runtime_data.options[OPT_SKIP_BACK] == 15.0
