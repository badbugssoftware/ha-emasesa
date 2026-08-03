"""Piezas comunes a las entidades de EMASESA."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, CONF_CONTRACT_NUMBER, DOMAIN
from .coordinator import EmasesaCoordinator

OFICINA_URL = "https://www.emasesaonline.com/oficina-online/web/home/"


def build_device_info(
    coordinator: EmasesaCoordinator, entry: ConfigEntry
) -> DeviceInfo:
    """Dispositivo único por contrato, con los datos reales del contador."""
    number = entry.data.get(CONF_CONTRACT_NUMBER, coordinator.contract_id)
    meter = (coordinator.data or {}).get("meter", {})
    return DeviceInfo(
        identifiers={(DOMAIN, coordinator.contract_id)},
        name=f"EMASESA {number}",
        manufacturer=meter.get("fabricante") or "EMASESA",
        model=meter.get("modelo"),
        serial_number=meter.get("numero_serie"),
        configuration_url=OFICINA_URL,
    )


# Identificador del sub-dispositivo que agrupaba los embalses hasta la 0.6.0.
# Se conserva sólo para poder retirarlo del registro: los embalses ya cuelgan
# del dispositivo del contrato, y un dispositivo de más para seis sensores que
# casi nadie mira era más estorbo que orden.
LEGACY_RESERVOIR_DEVICE = "_embalses"


class EmasesaEntity(CoordinatorEntity[EmasesaCoordinator]):
    """Base de todas las entidades: dispositivo, id único y acceso a datos.

    El `unique_id` se compone SIEMPRE como `<contrato>_<key>`. No cambies la
    `key` de una entidad ya publicada: Home Assistant la trataría como una
    entidad nueva y el usuario perdería su histórico.
    """

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(
        self,
        coordinator: EmasesaCoordinator,
        entry: ConfigEntry,
        key: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{coordinator.contract_id}_{key}"
        self._attr_device_info = build_device_info(coordinator, entry)

    @property
    def datos(self) -> dict[str, Any]:
        """Último dato del coordinator, nunca None.

        Antes del primer refresco correcto `coordinator.data` es None, así que
        sin esto cada propiedad tendría que repetir el `or {}`.
        """
        return self.coordinator.data or {}
