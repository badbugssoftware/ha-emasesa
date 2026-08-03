"""Piezas comunes a las entidades de EMASESA."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import CONF_CONTRACT_NUMBER, DOMAIN
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


def build_reservoir_device_info(
    coordinator: EmasesaCoordinator, entry: ConfigEntry
) -> DeviceInfo:
    """Sub-dispositivo que agrupa los embalses bajo el del contrato.

    Los embalses no son del suministro del usuario, son de la ciudad: colgarlos
    sueltos junto al consumo y las facturas los mezcla con lo que sí es suyo.
    Con `via_device` quedan anidados como sub-dispositivo, que es la forma que
    tiene Home Assistant de agrupar entidades relacionadas.
    """
    number = entry.data.get(CONF_CONTRACT_NUMBER, coordinator.contract_id)
    return DeviceInfo(
        identifiers={(DOMAIN, f"{coordinator.contract_id}_embalses")},
        name=f"Embalses EMASESA {number}",
        manufacturer="EMASESA",
        model="Sistema de abastecimiento",
        entry_type=DeviceEntryType.SERVICE,
        via_device=(DOMAIN, coordinator.contract_id),
    )
