"""De qué red de abastecimiento es un suministro, según dónde está.

SINAC publica la calidad del agua por red, y EMASESA tiene una por municipio.
Así que la red se deduce de la ubicación del suministro, con los límites
municipales oficiales, en vez de pedírsela a quien instala: una lista
obligatoria llega con la primera opción ya marcada, y era fácil quedarse con
"Alcalá de Guadaíra" sin darse cuenta.

Los límites van dentro de la integración (`limites_municipales.json`, del
Instituto Geográfico Nacional, CC BY 4.0): las coordenadas del suministro no
salen de Home Assistant.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

type Anillo = list[list[float]]

_FICHERO = Path(__file__).parent / "limites_municipales.json"


@lru_cache(maxsize=1)
def cargar_limites() -> dict[str, list[Anillo]]:
    """Límites de los municipios de EMASESA: código INE -> anillos [lat, lon].

    Lee de disco: desde Home Assistant hay que llamarla en un executor.
    """
    return json.loads(_FICHERO.read_text(encoding="utf-8"))["limites"]


def _dentro(latitud: float, longitud: float, anillo: Anillo) -> bool:
    """Trazado de rayos: ¿cae el punto dentro del anillo?"""
    dentro = False
    lat1, lon1 = anillo[-1]
    for lat2, lon2 in anillo:
        if (lat1 > latitud) != (lat2 > latitud) and longitud < lon1 + (lon2 - lon1) * (
            latitud - lat1
        ) / (lat2 - lat1):
            dentro = not dentro
        lat1, lon1 = lat2, lon2
    return dentro


def red_de(
    latitud: float | None,
    longitud: float | None,
    limites: dict[str, list[Anillo]],
) -> str | None:
    """Código INE del municipio de EMASESA en el que cae el punto, o None.

    Regla par-impar sobre todos los anillos del municipio, que vale tanto para
    los que tienen un enclave separado (Sevilla) como para un posible hueco.
    """
    if latitud is None or longitud is None:
        return None
    for codigo, anillos in limites.items():
        if sum(_dentro(latitud, longitud, a) for a in anillos) % 2:
            return codigo
    return None
