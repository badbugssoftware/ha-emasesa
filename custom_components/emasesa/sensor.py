"""Sensores de la integración EMASESA.

Las entidades se declaran como datos (`EmasesaSensorEntityDescription`), no
como una clase por sensor: cada una dice de dónde sale su valor (`value_fn`) y
sus atributos (`attrs_fn`). Añadir un sensor nuevo es añadir una entrada a
`SENSORES`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import EmasesaCoordinator
from .entity import EmasesaEntity

type Datos = dict[str, Any]


@dataclass(frozen=True, kw_only=True)
class EmasesaSensorEntityDescription(SensorEntityDescription):
    """Descripción de un sensor de EMASESA.

    `key` es además el sufijo del `unique_id` (`<contrato>_<key>`): cambiarla
    en una entidad ya publicada le borra el histórico al usuario.
    """

    value_fn: Callable[[Datos], StateType]
    attrs_fn: Callable[[Datos], dict[str, Any]] | None = None
    # Para entidades que dependen de datos que la API puede no traer.
    available_fn: Callable[[Datos], bool] | None = None


# --------------------------------------------------------------------------- #
# Extractores
# --------------------------------------------------------------------------- #
def _factura(d: Datos) -> dict[str, Any]:
    return d.get("factura", {}) or {}


def _periodo(d: Datos) -> dict[str, Any]:
    return d.get("periodo_facturacion", {}) or {}


def _embalses(d: Datos) -> dict[str, Any]:
    return d.get("embalses", {}) or {}


def _dias_hasta_proxima_factura(d: Datos) -> int | None:
    fecha = _periodo(d).get("proxima_factura")
    if not fecha:
        return None
    try:
        prox = datetime.strptime(str(fecha)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return max(0, (prox - dt_util.now().date()).days)


def _atributos_embalses(d: Datos) -> dict[str, Any]:
    """Estado conjunto y, dentro, el desglose de cada embalse.

    El desglose va aquí para que se vea de un vistazo a qué embalses se
    refiere sin tener que activar un sensor por cada uno. Lo que no da un
    atributo es histórico: para graficar la evolución de un embalse concreto
    hay que activar su sensor (vienen desactivados de fábrica).
    """
    e = _embalses(d)
    attrs: dict[str, Any] = {
        "fecha": e.get("fecha"),
        "volumen_hm3": e.get("vol_embalsado_hm3"),
        "capacidad_hm3": e.get("capacidad_hm3"),
        "por_embalse": e.get("por_embalse") or [],
    }
    attrs.update(e.get("detalle") or {})
    return attrs


# --------------------------------------------------------------------------- #
# Sensores del contrato
# --------------------------------------------------------------------------- #
SENSORES: tuple[EmasesaSensorEntityDescription, ...] = (
    # Lectura acumulada del contador. Es `total_increasing`, así que puede
    # usarse como fuente de "Consumo de agua" en el panel de Energía. NOTA: no
    # lo añadas a la vez que la estadística externa `emasesa:<contrato>_water`
    # — ambas representan el mismo contador y se contaría doble. Recomendado:
    # usa la estadística externa (tiene detalle horario correcto) y deja este
    # sensor para tarjetas y automatizaciones.
    EmasesaSensorEntityDescription(
        key="indice",
        translation_key="indice_contador",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=3,
        value_fn=lambda d: d.get("total_m3"),
        attrs_fn=lambda d: {
            "indice_litros": d.get("indice_l"),
            "fecha_dato": d.get("fecha"),
            "fecha_lectura_contador": (d.get("meter") or {}).get("fecha_lectura"),
            "numero_serie": (d.get("meter") or {}).get("numero_serie"),
            "modelo": (d.get("meter") or {}).get("modelo"),
            "telelectura_nbiot": (d.get("meter") or {}).get("nbiot"),
            "estadistica_historica": d.get("statistic_id"),
        },
    ),
    # Consumo del último día disponible. Es un valor diario que sube y baja
    # entre días, así que NO lleva state_class: evita que el recorder calcule
    # una "suma" de largo plazo incorrecta. El histórico correcto para Energía
    # lo aportan la estadística externa y el índice del contador.
    EmasesaSensorEntityDescription(
        key="consumo_hoy",
        translation_key="consumo_hoy",
        icon="mdi:water",
        native_unit_of_measurement=UnitOfVolume.LITERS,
        suggested_display_precision=0,
        value_fn=lambda d: d.get("consumo_hoy_l"),
        attrs_fn=lambda d: {
            "fecha": d.get("fecha"),
            "consumo_diurno_litros": d.get("consumo_diurno_l"),
            "consumo_nocturno_litros": d.get("consumo_nocturno_l"),
        },
    ),
    # Coste estimado del ciclo en curso, calculado por el simulador oficial de
    # EMASESA con el consumo real: cuota fija + tramos + saneamiento +
    # depuración + canon + IVA.
    EmasesaSensorEntityDescription(
        key="coste_periodo",
        translation_key="coste_periodo",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="EUR",
        suggested_display_precision=2,
        icon="mdi:cash",
        value_fn=lambda d: d.get("coste_periodo_eur"),
        attrs_fn=lambda d: {
            "consumo_periodo_m3": d.get("consumo_periodo_m3"),
            "precio_efectivo_eur_m3": d.get("precio_m3_eur"),
            "periodo_desde": _periodo(d).get("desde"),
            "proxima_factura": _periodo(d).get("proxima_factura"),
        },
    ),
    # Pensado para el coste del panel de Energía (opción "precio por m³").
    # Baja según consumes más, porque reparte la cuota fija entre más m³.
    EmasesaSensorEntityDescription(
        key="precio_m3",
        translation_key="precio_m3",
        native_unit_of_measurement="EUR/m³",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        icon="mdi:cash-multiple",
        value_fn=lambda d: d.get("precio_m3_eur"),
    ),
    EmasesaSensorEntityDescription(
        key="ultima_factura",
        translation_key="ultima_factura",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="EUR",
        suggested_display_precision=2,
        icon="mdi:file-document-outline",
        value_fn=lambda d: _factura(d).get("importe"),
        attrs_fn=lambda d: {
            "numero": _factura(d).get("numero"),
            "fecha_emision": _factura(d).get("fecha_emision"),
            "estado_cobro": _factura(d).get("estado_cobro"),
            "consumo_m3": _factura(d).get("consumo_m3"),
            "dias_facturados": _factura(d).get("dias"),
            "periodo_desde": _factura(d).get("periodo_desde"),
            "periodo_hasta": _factura(d).get("periodo_hasta"),
        },
    ),
    EmasesaSensorEntityDescription(
        key="deuda_pendiente",
        translation_key="deuda_pendiente",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="EUR",
        suggested_display_precision=2,
        icon="mdi:cash-clock",
        value_fn=lambda d: _factura(d).get("pendiente_total"),
    ),
    EmasesaSensorEntityDescription(
        key="consumo_medio",
        translation_key="consumo_medio",
        native_unit_of_measurement=UnitOfVolume.LITERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:chart-line",
        value_fn=lambda d: _periodo(d).get("consumo_medio_l_dia"),
        attrs_fn=lambda d: {
            "valoracion": _periodo(d).get("valoracion"),
            "valoracion_texto": _periodo(d).get("valoracion_texto"),
            "ultima_telelectura": _periodo(d).get("ultima_telelectura"),
        },
    ),
    EmasesaSensorEntityDescription(
        key="dias_proxima_factura",
        translation_key="dias_proxima_factura",
        native_unit_of_measurement="d",
        icon="mdi:calendar-clock",
        value_fn=_dias_hasta_proxima_factura,
        attrs_fn=lambda d: {
            "periodo_desde": _periodo(d).get("desde"),
            "proxima_factura": _periodo(d).get("proxima_factura"),
        },
    ),
    # Nivel conjunto de los embalses que abastecen a Sevilla.
    EmasesaSensorEntityDescription(
        key="embalses",
        translation_key="embalses",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:waves",
        value_fn=lambda d: _embalses(d).get("porc_llenado"),
        attrs_fn=_atributos_embalses,
    ),
)


def _descripcion_embalse(nombre: str) -> EmasesaSensorEntityDescription:
    """Descripción de un embalse concreto (Aracena, Zufre, La Minilla...).

    La lista la decide la API, así que estas descripciones se construyen en
    tiempo de ejecución. El nombre se muestra tal cual porque no hay clave de
    traducción posible para algo que no conocemos de antemano.

    Vienen DESACTIVADAS: seis sensores más para un dato que ya está en los
    atributos del sensor conjunto sobrecargan la lista de la mayoría. Quien
    quiera graficar un embalse suelto lo activa desde el dispositivo, y desde
    ese momento tiene histórico.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", nombre.lower()).strip("_")

    def _datos(d: Datos) -> dict[str, Any]:
        for e in _embalses(d).get("por_embalse") or []:
            if e.get("nombre") == nombre:
                return e
        return {}

    return EmasesaSensorEntityDescription(
        key=f"embalse_{slug}",
        name=nombre,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:water-well",
        entity_registry_enabled_default=False,
        value_fn=lambda d: _datos(d).get("porc_llenado"),
        available_fn=lambda d: bool(_datos(d)),
        attrs_fn=lambda d: {
            "embalse": nombre,
            "volumen_hm3": _datos(d).get("vol_embalsado_hm3"),
            "capacidad_hm3": _datos(d).get("capacidad_hm3"),
            "fecha": _embalses(d).get("fecha"),
        },
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Da de alta los sensores del contrato."""
    coordinator: EmasesaCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Un sensor por embalse, según lo que trajo el primer refresco.
    nombres = [
        e["nombre"]
        for e in (_embalses(coordinator.data or {}).get("por_embalse") or [])
        if e.get("nombre")
    ]
    async_add_entities(
        EmasesaSensor(coordinator, entry, descripcion)
        for descripcion in (*SENSORES, *(_descripcion_embalse(n) for n in nombres))
    )


class EmasesaSensor(EmasesaEntity, SensorEntity):
    """Sensor cuyo comportamiento sale entero de su descripción."""

    entity_description: EmasesaSensorEntityDescription

    def __init__(
        self,
        coordinator: EmasesaCoordinator,
        entry: ConfigEntry,
        description: EmasesaSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        comprobar = self.entity_description.available_fn
        return comprobar is None or comprobar(self.datos)

    @property
    def native_value(self) -> StateType:
        return self.entity_description.value_fn(self.datos)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if (attrs_fn := self.entity_description.attrs_fn) is None:
            return None
        return attrs_fn(self.datos)
