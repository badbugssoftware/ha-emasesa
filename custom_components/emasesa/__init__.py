"""Integración EMASESA (Aguas de Sevilla) para Home Assistant."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .api import EmasesaClient, EmasesaError
from .const import (
    CONF_CONTRACT_ID,
    CONF_DEVICE_ID,
    CONF_INCIDENT_RADIUS,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_PASSWORD,
    CONF_SCAN_MINUTES,
    CONF_USERNAME,
    DEFAULT_INCIDENT_RADIUS,
    DEFAULT_SCAN_MINUTES,
    DOMAIN,
    INITIAL_BACKFILL_DAYS,
    LEGACY_CONF_SCAN_HOURS,
    MAX_SCAN_MINUTES,
    MIN_SCAN_MINUTES,
    PLATFORMS,
)
from .coordinator import EmasesaCoordinator

_LOGGER = logging.getLogger(__name__)


@callback
def _migrate_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Convierte opciones de versiones anteriores.

    'scan_hours' (horas) se sustituyó por 'scan_minutes'. Sin esta migración
    el ajuste del usuario quedaba huérfano y se aplicaba el valor por defecto.
    """
    options = dict(entry.options)
    legacy = options.pop(LEGACY_CONF_SCAN_HOURS, None)
    if legacy is None:
        return
    if CONF_SCAN_MINUTES not in options:
        options[CONF_SCAN_MINUTES] = max(
            MIN_SCAN_MINUTES, min(int(legacy) * 60, MAX_SCAN_MINUTES)
        )
    _LOGGER.debug(
        "Migrada la opción %s=%s a %s=%s",
        LEGACY_CONF_SCAN_HOURS,
        legacy,
        CONF_SCAN_MINUTES,
        options.get(CONF_SCAN_MINUTES),
    )
    hass.config_entries.async_update_entry(entry, options=options)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Configura una entrada (un contrato de EMASESA)."""
    _migrate_options(hass, entry)
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
        entry.options.get(CONF_INCIDENT_RADIUS, DEFAULT_INCIDENT_RADIUS),
        entry.options.get(CONF_LATITUDE),
        entry.options.get(CONF_LONGITUDE),
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    _async_register_services(hass)
    return True


SERVICE_SIMULAR = "simular_factura"
SERVICE_RECARGAR = "recargar_historico"

_SIMULAR_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): cv.string,
        vol.Required("consumo"): vol.Coerce(float),
        vol.Optional("fecha_desde"): cv.date,
        vol.Optional("fecha_hasta"): cv.date,
    }
)
_RECARGAR_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): cv.string,
        vol.Optional("dias", default=INITIAL_BACKFILL_DAYS): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=365)
        ),
    }
)


def _get_coordinator(hass: HomeAssistant, entry_id: str) -> EmasesaCoordinator:
    """Coordinator de la entrada indicada, o error legible para el usuario."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry_id)
    if coordinator is None:
        raise ServiceValidationError(
            f"No hay ninguna integración EMASESA cargada con id {entry_id}"
        )
    return coordinator


@callback
def _async_register_services(hass: HomeAssistant) -> None:
    """Registra los servicios del dominio una sola vez."""
    if hass.services.has_service(DOMAIN, SERVICE_SIMULAR):
        return

    async def _simular(call: ServiceCall) -> ServiceResponse:
        coordinator = _get_coordinator(hass, call.data["config_entry_id"])
        periodo = (coordinator.data or {}).get("periodo_facturacion", {})
        desde: date | str | None = call.data.get("fecha_desde") or periodo.get("desde")
        hasta: date | str | None = call.data.get("fecha_hasta") or dt_util.now().date()
        if not desde:
            raise ServiceValidationError(
                "No se conoce el inicio del periodo; indica 'fecha_desde'."
            )
        try:
            sim = await coordinator.client.simulate_invoice(
                coordinator.contract_id, call.data["consumo"], desde, hasta
            )
        except EmasesaError as err:
            raise HomeAssistantError(f"EMASESA no pudo simular: {err}") from err
        conceptos: list[dict[str, Any]] = [
            {
                "concepto": c.get("concepto"),
                "unidades": c.get("unidades"),
                "precio_unitario": c.get("precioUnitario"),
                "total_con_iva": c.get("totalConIVA"),
            }
            for c in (sim.get("conceptosFacturables") or [])
        ]
        return {
            "importe": sim.get("importe"),
            "consumo": sim.get("consumo"),
            "dias": sim.get("dias"),
            "conceptos": conceptos,
        }

    async def _recargar(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call.data["config_entry_id"])
        await coordinator.async_reload_history(call.data["dias"])

    hass.services.async_register(
        DOMAIN,
        SERVICE_SIMULAR,
        _simular,
        schema=_SIMULAR_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RECARGAR, _recargar, schema=_RECARGAR_SCHEMA
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Descarga la entrada."""
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id, None)
        return True
    return False


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Recarga cuando cambian las opciones (p.ej. intervalo de sondeo)."""
    await hass.config_entries.async_reload(entry.entry_id)
