"""Sensores de la integración EMASESA."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, CONF_CONTRACT_NUMBER, CONF_SUPPLY_ADDRESS, DOMAIN
from .coordinator import EmasesaCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EmasesaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            EmasesaMeterSensor(coordinator, entry),
            EmasesaTodaySensor(coordinator, entry),
        ]
    )


class EmasesaBaseSensor(CoordinatorEntity[EmasesaCoordinator], SensorEntity):
    """Base con device_info compartido por contrato/contador."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator: EmasesaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        number = entry.data.get(CONF_CONTRACT_NUMBER, coordinator.contract_id)
        meter = coordinator.data.get("meter", {}) if coordinator.data else {}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.contract_id)},
            name=f"EMASESA {number}",
            manufacturer=meter.get("fabricante") or "EMASESA",
            model=meter.get("modelo"),
            serial_number=meter.get("numero_serie"),
            configuration_url="https://www.emasesaonline.com/oficina-online/web/home/",
        )


class EmasesaMeterSensor(EmasesaBaseSensor):
    """Lectura acumulada del contador (m³).

    Es `total_increasing`, así que puede usarse como fuente de "Consumo de agua"
    en el panel de Energía. NOTA: no lo añadas a la vez que la estadística
    externa `emasesa:<contrato>_water` — ambas representan el mismo contador y se
    contaría doble. Recomendado: usa la estadística externa (tiene detalle
    horario correcto) y deja este sensor para tarjetas/automatizaciones.
    """

    _attr_translation_key = "indice_contador"
    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
    _attr_suggested_display_precision = 3

    def __init__(self, coordinator: EmasesaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{coordinator.contract_id}_indice"

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data or {}
        return data.get("total_m3")

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        meter = data.get("meter", {})
        return {
            "indice_litros": data.get("indice_l"),
            "fecha_dato": data.get("fecha"),
            "fecha_lectura_contador": meter.get("fecha_lectura"),
            "numero_serie": meter.get("numero_serie"),
            "modelo": meter.get("modelo"),
            "telelectura_nbiot": meter.get("nbiot"),
            "estadistica_historica": self.coordinator.statistic_id,
        }


class EmasesaTodaySensor(EmasesaBaseSensor):
    """Consumo del último día disponible (litros).

    Es un valor diario que sube y baja entre días, así que NO lleva state_class
    (evita que el recorder calcule una 'suma' de largo plazo incorrecta). El
    histórico correcto para Energía lo aportan la estadística externa del
    coordinator y el sensor del índice del contador.
    """

    _attr_translation_key = "consumo_hoy"
    _attr_icon = "mdi:water"
    _attr_native_unit_of_measurement = UnitOfVolume.LITERS
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: EmasesaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{coordinator.contract_id}_consumo_hoy"

    @property
    def native_value(self) -> int | None:
        data = self.coordinator.data or {}
        return data.get("consumo_hoy_l")

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        return {
            "fecha": data.get("fecha"),
            "consumo_diurno_litros": data.get("consumo_diurno_l"),
            "consumo_nocturno_litros": data.get("consumo_nocturno_l"),
        }
