"""Calidad del agua del grifo, según los boletines que EMASESA notifica a SINAC.

Va aparte del coordinator del contrato a propósito:

- SINAC tarda unos 30 s en responder. Dentro del ciclo principal retrasaría
  el arranque de Home Assistant y cada actualización del contador.
- Es otra fuente, con otro ritmo (boletines cada pocos días) y otros fallos:
  que SINAC esté caído no debe dejar sin datos al consumo, ni al revés.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EmasesaError, get_sinac_detail, parse_sinac_detail
from .const import (
    CALIDAD_INTERVAL,
    CALIDAD_INTERVAL_REINTENTO,
    CONF_RED_SINAC,
    DOMAIN,
    REDES_SINAC,
)

_LOGGER = logging.getLogger(__name__)


def esquema_red(por_defecto: str | None = None) -> vol.Schema:
    """Selector de red, común al alta, las opciones y la reparación."""
    redes = {
        codigo: nombre
        for codigo, (nombre, _) in sorted(REDES_SINAC.items(), key=lambda r: r[1][0])
    }
    clave = (
        vol.Required(CONF_RED_SINAC, default=por_defecto)
        if por_defecto in REDES_SINAC
        else vol.Required(CONF_RED_SINAC)
    )
    return vol.Schema({clave: vol.In(redes)})


class EmasesaCalidadCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Consulta en SINAC la ficha de la red que abastece al suministro."""

    def __init__(self, hass: HomeAssistant, cod_municipio: str) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_calidad_{cod_municipio}",
            update_interval=CALIDAD_INTERVAL,
        )
        self.cod_municipio = cod_municipio
        self.nombre_red, self.id_red = REDES_SINAC[cod_municipio]

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            page = await get_sinac_detail(
                async_get_clientsession(self.hass), self.cod_municipio, self.id_red
            )
            datos = parse_sinac_detail(page)
        except EmasesaError as err:
            self.update_interval = CALIDAD_INTERVAL_REINTENTO
            raise UpdateFailed(str(err)) from err
        self.update_interval = CALIDAD_INTERVAL
        datos["red"] = self.nombre_red
        return datos
