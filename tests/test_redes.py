"""Tests de `custom_components/emasesa/redes.py`: la red, según la ubicación.

Geometría pura, sin Home Assistant: se cargan los límites municipales del IGN
que van dentro de la integración y se comprueba dónde cae cada punto.
"""

from __future__ import annotations

import pytest

from custom_components.emasesa.const import REDES_SINAC
from custom_components.emasesa.redes import cargar_limites, red_de

# El centro de cada pueblo: no hay duda de en qué municipio está.
CENTROS = {
    "41004": (37.338, -5.840),  # Alcalá de Guadaíra
    "41005": (37.518, -5.979),  # Alcalá del Río
    "41021": (37.402, -6.033),  # Camas
    "41034": (37.288, -6.054),  # Coria del Río
    "41038": (37.283, -5.922),  # Dos Hermanas
    "41043": (37.625, -6.172),  # El Garrobo
    "41058": (37.372, -5.748),  # Mairena del Alcor
    "41079": (37.268, -6.063),  # La Puebla del Río
    "41081": (37.487, -5.981),  # La Rinconada
    "41083": (37.727, -6.176),  # El Ronquillo
    "41086": (37.366, -6.027),  # San Juan de Aznalfarache
    "41091": (37.3891, -5.9845),  # Sevilla
}


def test_hay_limites_para_todas_las_redes_de_emasesa():
    assert set(cargar_limites()) == set(REDES_SINAC) == set(CENTROS)


@pytest.mark.parametrize("codigo", sorted(CENTROS))
def test_el_centro_de_cada_municipio_cae_en_su_red(codigo):
    assert red_de(*CENTROS[codigo], cargar_limites()) == codigo


def test_triana_es_sevilla_aunque_este_al_otro_lado_del_rio():
    assert red_de(37.383, -6.004, cargar_limites()) == "41091"


@pytest.mark.parametrize(
    "punto",
    [
        (37.375, -6.045),  # Tomares: pegado a Sevilla, pero no es de EMASESA
        (40.4168, -3.7038),  # Madrid
        (0.0, 0.0),  # ubicación sin configurar
        (None, None),
    ],
)
def test_fuera_de_los_municipios_de_emasesa_no_hay_red(punto):
    assert red_de(*punto, cargar_limites()) is None
