"""Sensores de la integración EMASESA."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import ATTRIBUTION, DOMAIN
from .coordinator import EmasesaCoordinator
from .entity import build_device_info


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
            EmasesaCostSensor(coordinator, entry),
            EmasesaPriceSensor(coordinator, entry),
            EmasesaInvoiceSensor(coordinator, entry),
            EmasesaDebtSensor(coordinator, entry),
            EmasesaDailyAverageSensor(coordinator, entry),
            EmasesaNextInvoiceSensor(coordinator, entry),
            EmasesaReservoirSensor(coordinator, entry),
        ]
    )


class EmasesaBaseSensor(CoordinatorEntity[EmasesaCoordinator], SensorEntity):
    """Base con device_info compartido por contrato/contador."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator: EmasesaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = build_device_info(coordinator, entry)


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


class EmasesaCostSensor(EmasesaBaseSensor):
    """Coste estimado del periodo de facturación en curso (€).

    Lo calcula el simulador oficial de EMASESA con tu consumo real del ciclo,
    aplicando cuota fija + tramos + saneamiento + depuración + canon + IVA.
    """

    _attr_translation_key = "coste_periodo"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "EUR"
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:cash"

    def __init__(self, coordinator: EmasesaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{coordinator.contract_id}_coste_periodo"

    @property
    def native_value(self) -> float | None:
        return (self.coordinator.data or {}).get("coste_periodo_eur")

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        periodo = data.get("periodo_facturacion", {}) or {}
        return {
            "consumo_periodo_m3": data.get("consumo_periodo_m3"),
            "precio_efectivo_eur_m3": data.get("precio_m3_eur"),
            "periodo_desde": periodo.get("desde"),
            "proxima_factura": periodo.get("proxima_factura"),
        }


class EmasesaPriceSensor(EmasesaBaseSensor):
    """Precio efectivo del agua (€/m³) del periodo en curso.

    Pensado para el coste del panel de Energía (opción "precio por m³").
    Baja según consumes más, porque reparte la cuota fija entre más m³.
    """

    _attr_translation_key = "precio_m3"
    _attr_native_unit_of_measurement = "EUR/m³"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 4
    _attr_icon = "mdi:cash-multiple"

    def __init__(self, coordinator: EmasesaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{coordinator.contract_id}_precio_m3"

    @property
    def native_value(self) -> float | None:
        return (self.coordinator.data or {}).get("precio_m3_eur")


class EmasesaInvoiceSensor(EmasesaBaseSensor):
    """Importe de la última factura emitida."""

    _attr_translation_key = "ultima_factura"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "EUR"
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:file-document-outline"

    def __init__(self, coordinator: EmasesaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{coordinator.contract_id}_ultima_factura"

    @property
    def native_value(self) -> float | None:
        return (self.coordinator.data or {}).get("factura", {}).get("importe")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        f = (self.coordinator.data or {}).get("factura", {})
        return {
            "numero": f.get("numero"),
            "fecha_emision": f.get("fecha_emision"),
            "estado_cobro": f.get("estado_cobro"),
            "consumo_m3": f.get("consumo_m3"),
            "dias_facturados": f.get("dias"),
            "periodo_desde": f.get("periodo_desde"),
            "periodo_hasta": f.get("periodo_hasta"),
        }


class EmasesaDebtSensor(EmasesaBaseSensor):
    """Importe pendiente de pago (suma de recibos no cobrados)."""

    _attr_translation_key = "deuda_pendiente"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "EUR"
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:cash-clock"

    def __init__(self, coordinator: EmasesaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{coordinator.contract_id}_deuda_pendiente"

    @property
    def native_value(self) -> float | None:
        return (self.coordinator.data or {}).get("factura", {}).get("pendiente_total")


class EmasesaDailyAverageSensor(EmasesaBaseSensor):
    """Consumo medio diario del periodo en curso (litros)."""

    _attr_translation_key = "consumo_medio"
    _attr_native_unit_of_measurement = UnitOfVolume.LITERS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:chart-line"

    def __init__(self, coordinator: EmasesaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{coordinator.contract_id}_consumo_medio"

    @property
    def native_value(self) -> float | None:
        p = (self.coordinator.data or {}).get("periodo_facturacion", {})
        return p.get("consumo_medio_l_dia")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        p = (self.coordinator.data or {}).get("periodo_facturacion", {})
        return {
            "valoracion": p.get("valoracion"),
            "valoracion_texto": p.get("valoracion_texto"),
            "ultima_telelectura": p.get("ultima_telelectura"),
        }


class EmasesaNextInvoiceSensor(EmasesaBaseSensor):
    """Días que faltan para la próxima facturación."""

    _attr_translation_key = "dias_proxima_factura"
    _attr_native_unit_of_measurement = "d"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator: EmasesaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{coordinator.contract_id}_dias_proxima_factura"

    @property
    def native_value(self) -> int | None:
        p = (self.coordinator.data or {}).get("periodo_facturacion", {})
        fecha = p.get("proxima_factura")
        if not fecha:
            return None
        try:
            prox = datetime.strptime(str(fecha)[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
        return max(0, (prox - dt_util.now().date()).days)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        p = (self.coordinator.data or {}).get("periodo_facturacion", {})
        return {
            "periodo_desde": p.get("desde"),
            "proxima_factura": p.get("proxima_factura"),
        }


class EmasesaReservoirSensor(EmasesaBaseSensor):
    """Nivel conjunto de los embalses que abastecen a Sevilla."""

    _attr_translation_key = "embalses"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:waves"

    def __init__(self, coordinator: EmasesaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{coordinator.contract_id}_embalses"

    @property
    def native_value(self) -> float | None:
        return (self.coordinator.data or {}).get("embalses", {}).get("porc_llenado")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        e = (self.coordinator.data or {}).get("embalses", {})
        attrs: dict[str, Any] = {
            "fecha": e.get("fecha"),
            "volumen_hm3": e.get("vol_embalsado_hm3"),
            "capacidad_hm3": e.get("capacidad_hm3"),
        }
        attrs.update(e.get("detalle") or {})
        return attrs
