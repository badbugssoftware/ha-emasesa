"""Diagnósticos de la integración EMASESA (con datos personales redactados)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import EmasesaCoordinator

# Nunca deben salir del sistema del usuario.
REDACT_CONFIG = {
    "usuario",
    "contrasena",
    "id_dispositivo",
    "direccion_suministro",
}
REDACT_DATA = {
    "direccion",
    "titular",
    "numero",
    "nif",
    "email",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Vuelca configuración y último dato del coordinator, sin datos sensibles."""
    coordinator: EmasesaCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = dict(coordinator.data or {})

    # Las incidencias llevan direcciones de terceros: solo dejamos el recuento.
    incidencias = dict(data.get("incidencias") or {})
    if "cercanas" in incidencias:
        incidencias["cercanas"] = len(incidencias["cercanas"])
    data["incidencias"] = incidencias

    return {
        "entry": {
            "title": entry.title,
            "version": entry.version,
            "data": async_redact_data(dict(entry.data), REDACT_CONFIG),
            "options": dict(entry.options),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
            "statistic_id": coordinator.statistic_id,
            "cost_statistic_id": coordinator.cost_statistic_id,
            "incident_radius_m": coordinator.incident_radius_m,
        },
        "data": async_redact_data(data, REDACT_DATA),
    }
