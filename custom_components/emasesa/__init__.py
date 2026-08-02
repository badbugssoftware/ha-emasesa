"""Integración EMASESA (Aguas de Sevilla) para Home Assistant."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EmasesaClient
from .const import (
    CONF_CONTRACT_ID,
    CONF_DEVICE_ID,
    CONF_PASSWORD,
    CONF_SCAN_MINUTES,
    CONF_USERNAME,
    DEFAULT_SCAN_MINUTES,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import EmasesaCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Configura una entrada (un contrato de EMASESA)."""
    client = EmasesaClient(
        async_get_clientsession(hass),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        entry.data[CONF_DEVICE_ID],
    )

    scan_minutes = entry.options.get(CONF_SCAN_MINUTES, DEFAULT_SCAN_MINUTES)
    coordinator = EmasesaCoordinator(
        hass,
        client,
        entry.data[CONF_CONTRACT_ID],
        timedelta(minutes=scan_minutes),
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Descarga la entrada."""
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id, None)
        return True
    return False


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Recarga cuando cambian las opciones (p.ej. intervalo de sondeo)."""
    await hass.config_entries.async_reload(entry.entry_id)
