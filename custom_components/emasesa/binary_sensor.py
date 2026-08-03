"""Sensores binarios de EMASESA: fuga, avería del contador e incidencias."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import EmasesaCoordinator
from .entity import EmasesaEntity

type Datos = dict[str, Any]


@dataclass(frozen=True, kw_only=True)
class EmasesaBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Descripción de un sensor binario.

    `is_on_fn` puede devolver None: es la forma de decir "no lo sé todavía",
    que no es lo mismo que "no". `key` es el sufijo del `unique_id` y no debe
    cambiar una vez publicada la entidad.
    """

    is_on_fn: Callable[[Datos], bool | None]
    attrs_fn: Callable[[Datos], dict[str, Any]] | None = None


def _fuga(d: Datos) -> dict[str, Any]:
    return d.get("fuga", {}) or {}


def _incidencias(d: Datos) -> dict[str, Any]:
    return d.get("incidencias", {}) or {}


def _hay_fuga(d: Datos) -> bool | None:
    """Posible fuga: consumo continuo durante la madrugada.

    Si en la franja nocturna no hay NINGUNA hora con consumo cero durante
    varias noches seguidas, lo más probable es que haya un goteo permanente.

    Devuelve None mientras no haya noches completas que analizar: decir "sin
    fuga" sin haber mirado ninguna noche es afirmar algo que no se sabe. Sin
    telelectura horaria, o con menos de tres noches de histórico, el sensor
    se queda en "desconocido".
    """
    fuga = _fuga(d)
    if not fuga.get("analizado"):
        return None
    return bool(fuga.get("detectada"))


def _atributos_incidencias(d: Datos) -> dict[str, Any]:
    inc = _incidencias(d)
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


SENSORES: tuple[EmasesaBinarySensorEntityDescription, ...] = (
    EmasesaBinarySensorEntityDescription(
        key="posible_fuga",
        translation_key="posible_fuga",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:water-alert",
        is_on_fn=_hay_fuga,
        attrs_fn=lambda d: {
            "analizado": bool(_fuga(d).get("analizado")),
            "noches_analizadas": _fuga(d).get("noches"),
            "consumo_minimo_nocturno_l": _fuga(d).get("min_l_h"),
            "desde": _fuga(d).get("desde"),
        },
    ),
    # El contador está en avería y EMASESA factura con consumo estimado.
    EmasesaBinarySensorEntityDescription(
        key="averia_contador",
        translation_key="averia_contador",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda d: bool(d.get("averia_estimacion")),
    ),
    # Hay una orden de trabajo o incidencia pendiente en tu suministro.
    EmasesaBinarySensorEntityDescription(
        key="incidencia_pendiente",
        translation_key="incidencia_pendiente",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda d: bool(d.get("incidencia_pendiente")),
    ),
    # Incidencia de la red de EMASESA cerca de la vivienda. Es el único dato
    # en tiempo real de la API: sirve para avisar de cortes o salideros y, por
    # ejemplo, cancelar el riego.
    EmasesaBinarySensorEntityDescription(
        key="incidencia_cercana",
        translation_key="incidencia_cercana",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:pipe-leak",
        is_on_fn=lambda d: bool(_incidencias(d).get("cercanas")),
        attrs_fn=_atributos_incidencias,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Da de alta los sensores binarios del contrato."""
    coordinator: EmasesaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        EmasesaBinarySensor(coordinator, entry, descripcion) for descripcion in SENSORES
    )


class EmasesaBinarySensor(EmasesaEntity, BinarySensorEntity):
    """Sensor binario cuyo comportamiento sale entero de su descripción."""

    entity_description: EmasesaBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: EmasesaCoordinator,
        entry: ConfigEntry,
        description: EmasesaBinarySensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.is_on_fn(self.datos)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if (attrs_fn := self.entity_description.attrs_fn) is None:
            return None
        return attrs_fn(self.datos)
