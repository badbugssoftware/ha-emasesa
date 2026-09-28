"""Reparaciones: elegir la red de abastecimiento en instalaciones existentes.

Quien instaló la integración antes de que existiera la calidad del agua no
eligió ninguna red. En vez de suponer una, se le pide desde Reparaciones; al
elegirla, se guarda en las opciones y la entrada se recarga sola (el listener
de opciones), que es cuando aparecen las entidades de calidad.
"""

from __future__ import annotations

from typing import Any

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant

from .calidad import esquema_red
from .const import CONF_RED_SINAC


class ElegirRedFlow(RepairsFlow):
    """Pide la red de SINAC de un contrato y la guarda en sus opciones."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        # Home Assistant abre el flujo con {"issue_id": ...} como user_input,
        # no con None: el formulario va en un paso aparte para no confundir
        # esa entrada con la red elegida.
        return await self.async_step_red()

    async def async_step_red(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            # El contrato se borró con el aviso abierto: nada que arreglar.
            return self.async_abort(reason="entry_not_found")
        if user_input is not None:
            self.hass.config_entries.async_update_entry(
                entry,
                options={**entry.options, CONF_RED_SINAC: user_input[CONF_RED_SINAC]},
            )
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="red",
            data_schema=esquema_red(),
            description_placeholders={"contrato": entry.title},
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Punto de entrada que usa Home Assistant al pulsar "Arreglar"."""
    return ElegirRedFlow(str((data or {})["entry_id"]))
