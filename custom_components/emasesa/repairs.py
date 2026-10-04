"""Reparaciones: marcar en el mapa dónde está el suministro.

El análisis del agua es distinto en cada municipio, así que hay que saber
dónde está el suministro. Quien instaló la integración antes de que lo
preguntara no lo ha dicho nunca, y la ubicación de Home Assistant no tiene por
qué ser la del contrato. Se le pide desde Reparaciones; al marcar el punto se
guarda en las opciones y la entrada se recarga sola (el listener de opciones),
que es cuando aparecen las entidades de calidad.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant

from .config_flow import esquema_ubicacion, ubicacion_marcada


class MarcarSuministroFlow(RepairsFlow):
    """Pide en un mapa la ubicación del suministro y la guarda en las opciones."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        # Home Assistant abre el flujo con {"issue_id": ...} como user_input,
        # no con None: el formulario va en un paso aparte para no confundir
        # esa entrada con el punto marcado.
        return await self.async_step_ubicacion()

    async def async_step_ubicacion(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            # El contrato se borró con el aviso abierto: nada que arreglar.
            return self.async_abort(reason="entry_not_found")
        errors: dict[str, str] = {}
        if user_input is not None:
            marcada = await ubicacion_marcada(self.hass, user_input.get("ubicacion"))
            if marcada is not None:
                self.hass.config_entries.async_update_entry(
                    entry, options={**entry.options, **marcada}
                )
                return self.async_create_entry(data={})
            errors["base"] = "fuera_de_emasesa"
        return self.async_show_form(
            step_id="ubicacion",
            data_schema=vol.Schema(esquema_ubicacion(self.hass, entry.options)),
            description_placeholders={"contrato": entry.title},
            errors=errors,
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Punto de entrada que usa Home Assistant al pulsar "Arreglar"."""
    return MarcarSuministroFlow(str((data or {})["entry_id"]))
