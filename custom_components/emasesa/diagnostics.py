"""Diagnósticos de la integración EMASESA (con datos personales redactados)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import EmasesaCoordinator

# Este volcado es lo que se adjunta a una incidencia pública en GitHub. Nada de
# lo que identifique a la persona o su casa puede salir en él.
REDACT_CONFIG = {
    "usuario",
    "contrasena",
    "id_dispositivo",
    "direccion_suministro",
    # El contrato identifica la cuenta y va ligado al domicilio.
    "contrato_id",
    "contrato_numero",
    # Ubicación del suministro, en las opciones: son las coordenadas de la casa.
    "latitude",
    "longitude",
}
REDACT_DATA = {
    "direccion",
    "titular",
    "numero",
    "nif",
    "email",
    "contract_id",
    "statistic_id",
    "numero_serie",
}


def _entrada(entry: ConfigEntry) -> dict[str, Any]:
    """La entrada de configuración, sin datos personales.

    El título es «EMASESA <número de contrato>», así que tampoco puede salir.
    """
    return {
        "title": REDACTED,
        "version": entry.version,
        "state": str(entry.state),
        "data": async_redact_data(dict(entry.data), REDACT_CONFIG),
        "options": async_redact_data(dict(entry.options), REDACT_CONFIG),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Vuelca configuración y último dato del coordinator, sin datos sensibles."""
    coordinator: EmasesaCoordinator | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )
    if coordinator is None:
        # La entrada no llegó a cargar: es justo cuando más falta hace el
        # diagnóstico, así que se devuelve lo que hay en vez de fallar.
        return {"entry": _entrada(entry), "coordinator": None}
    data = dict(coordinator.data or {})

    # Las incidencias llevan direcciones de terceros: solo dejamos el recuento.
    incidencias = dict(data.get("incidencias") or {})
    if "cercanas" in incidencias:
        incidencias["cercanas"] = len(incidencias["cercanas"])
    data["incidencias"] = incidencias

    # Los identificadores de las estadísticas llevan el contrato dentro; se
    # conserva la forma (`emasesa:…_water`), que sí ayuda a diagnosticar.
    contrato = coordinator.contract_id
    calidad = coordinator.calidad
    datos_calidad = (calidad.data or {}) if calidad else {}
    return {
        "entry": _entrada(entry),
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
            "statistic_id": coordinator.statistic_id.replace(contrato, REDACTED),
            "cost_statistic_id": coordinator.cost_statistic_id.replace(
                contrato, REDACTED
            ),
            "incident_radius_m": coordinator.incident_radius_m,
        },
        # Sólo el estado de la consulta: los valores analíticos son públicos y
        # no aportan nada a un diagnóstico. La red es el municipio, no la casa.
        "calidad": {
            "red": calidad.nombre_red,
            "last_update_success": calidad.last_update_success,
            "parametros": len(datos_calidad.get("parametros") or {}),
            "ultimo_control": datos_calidad.get("ultimo_control"),
        }
        if calidad
        else None,
        "data": async_redact_data(data, REDACT_DATA),
    }
