"""Piezas comunes a las entidades de EMASESA."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo

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
