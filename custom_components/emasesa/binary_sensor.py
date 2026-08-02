"""Sensores binarios de EMASESA: fuga, avería del contador e incidencias."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import EmasesaCoordinator
from .entity import build_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Da de alta los sensores binarios del contrato."""
    coordinator: EmasesaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            EmasesaLeakSensor(coordinator, entry),
            EmasesaMeterFaultSensor(coordinator, entry),
            EmasesaPendingIssueSensor(coordinator, entry),
            EmasesaNetworkIncidentSensor(coordinator, entry),
        ]
    )


class EmasesaBaseBinarySensor(
    CoordinatorEntity[EmasesaCoordinator], BinarySensorEntity
):
    """Comparte el dispositivo con los sensores normales."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator: EmasesaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_device_info = build_device_info(coordinator, entry)


class EmasesaLeakSensor(EmasesaBaseBinarySensor):
    """Posible fuga: consumo continuo durante la madrugada.

    Si en la franja nocturna no hay NINGUNA hora con consumo cero durante
    varias noches seguidas, lo más probable es que haya un goteo permanente.
    """

    _attr_translation_key = "posible_fuga"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:water-alert"

    def __init__(self, coordinator: EmasesaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{coordinator.contract_id}_posible_fuga"

    @property
    def is_on(self) -> bool | None:
        """None mientras no haya noches completas que analizar.

        Decir "sin fuga" sin haber mirado ninguna noche es afirmar algo que no
        se sabe: sin telelectura horaria, o con menos de tres noches de
        histórico, el sensor queda en "desconocido".
        """
        fuga = (self.coordinator.data or {}).get("fuga", {})
        if not fuga.get("analizado"):
            return None
        return bool(fuga.get("detectada"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        f = (self.coordinator.data or {}).get("fuga", {})
        return {
            "analizado": bool(f.get("analizado")),
            "noches_analizadas": f.get("noches"),
            "consumo_minimo_nocturno_l": f.get("min_l_h"),
            "desde": f.get("desde"),
        }


class EmasesaMeterFaultSensor(EmasesaBaseBinarySensor):
    """El contador está en avería y EMASESA estima el consumo."""

    _attr_translation_key = "averia_contador"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: EmasesaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{coordinator.contract_id}_averia_contador"

    @property
    def is_on(self) -> bool:
        return bool((self.coordinator.data or {}).get("averia_estimacion"))


class EmasesaPendingIssueSensor(EmasesaBaseBinarySensor):
    """Hay una orden de trabajo o incidencia pendiente en tu suministro."""

    _attr_translation_key = "incidencia_pendiente"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: EmasesaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{coordinator.contract_id}_incidencia_pendiente"

    @property
    def is_on(self) -> bool:
        return bool((self.coordinator.data or {}).get("incidencia_pendiente"))


class EmasesaNetworkIncidentSensor(EmasesaBaseBinarySensor):
    """Incidencia de la red de EMASESA cerca de la vivienda.

    Único dato en tiempo real de la API: sirve para avisar de cortes o
    salideros y, por ejemplo, cancelar el riego.
    """

    _attr_translation_key = "incidencia_cercana"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:pipe-leak"

    def __init__(self, coordinator: EmasesaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{coordinator.contract_id}_incidencia_cercana"

    @property
    def is_on(self) -> bool:
        inc = (self.coordinator.data or {}).get("incidencias", {})
        return bool(inc.get("cercanas"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        inc = (self.coordinator.data or {}).get("incidencias", {})
        cercanas = inc.get("cercanas") or []
        mas_cercana = cercanas[0] if cercanas else {}
        return {
            "numero": len(cercanas),
            "radio_m": inc.get("radio_m"),
            "total_ciudad": inc.get("total_ciudad"),
            "mas_cercana": mas_cercana.get("categoria"),
            "distancia_m": mas_cercana.get("distancia_m"),
            "direccion": mas_cercana.get("direccion"),
            "incidencias": cercanas[:10],
        }
