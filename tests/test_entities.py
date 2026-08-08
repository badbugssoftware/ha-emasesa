"""Tests de las entidades (sensor y binary_sensor).

Montan la integración de verdad con un coordinator que devuelve un payload
fijo, así que cubren el camino completo: `async_setup_entry` → plataformas →
descripciones → estado publicado.

El test que más importa aquí es `test_los_unique_id_no_cambian`: los
identificadores de las entidades son el contrato con las instalaciones que ya
existen. Si cambian, Home Assistant crea entidades nuevas y el usuario pierde
su histórico.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.emasesa.const import (
    CONF_CONTRACT_ID,
    CONF_CONTRACT_NUMBER,
    CONF_DEVICE_ID,
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
)
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import CONTRACT_ID, PASSWORD, USERNAME


@pytest.fixture(autouse=True)
async def _home_assistant(recorder_mock: Any, enable_custom_integrations: Any) -> None:
    """`recorder_mock` primero: la integración lo declara como dependencia."""
    return


# --------------------------------------------------------------------------- #
# Payload de ejemplo
# --------------------------------------------------------------------------- #
DATOS: dict[str, Any] = {
    "contract_id": CONTRACT_ID,
    "statistic_id": f"{DOMAIN}:{CONTRACT_ID}_water",
    "coste_periodo_eur": 23.47,
    "precio_m3_eur": 2.6078,
    "consumo_periodo_m3": 9.0,
    "periodo_facturacion": {
        "desde": "2026-06-15",
        "proxima_factura": "2026-08-15",
        "consumo_medio_l_dia": 187.0,
        "valoracion": "EFICIENTE",
        "valoracion_texto": "Tu consumo es eficiente",
        "ultima_telelectura": "2026-07-31",
    },
    "factura": {
        "importe": 41.22,
        "numero": "F2026-000123",
        "fecha_emision": "2026-06-14",
        "estado_cobro": "COBRADA",
        "consumo_m3": 17.0,
        "dias": 61,
        "periodo_desde": "2026-04-14",
        "periodo_hasta": "2026-06-13",
        "pendiente_total": 0.0,
    },
    "embalses": {
        "fecha": "2026-08-01",
        "porc_llenado": 80.4,
        "vol_embalsado_hm3": 179.6,
        "capacidad_hm3": 223.4,
        "por_embalse": [
            {
                "nombre": "Aracena",
                "porc_llenado": 76.2,
                "vol_embalsado_hm3": 96.4,
                "capacidad_hm3": 126.5,
            },
            {
                "nombre": "La Minilla",
                "porc_llenado": 91.3,
                "vol_embalsado_hm3": 53.1,
                "capacidad_hm3": 58.2,
            },
        ],
    },
    "incidencias": {
        "radio_m": 1000,
        "total_ciudad": 12,
        "cercanas": [
            {
                "categoria": "Avería en la red",
                "distancia_m": 320,
                "direccion": "C/ EJEMPLO",
            }
        ],
    },
    "fuga": {
        "analizado": True,
        "detectada": False,
        "noches": 3,
        "min_l_h": 0,
        "desde": "2026-07-29",
    },
    "averia_estimacion": False,
    "incidencia_pendiente": False,
    "fecha": "2026-07-31",
    "consumo_hoy_l": 16,
    "consumo_diurno_l": 15,
    "consumo_nocturno_l": 1,
    "indice_l": 443601,
    "total_m3": 443.601,
    "meter": {
        "indice_m3": 443,
        "fecha_lectura": "2026-07-31",
        "fabricante": "Contazara",
        "modelo": "CZ2000",
        "numero_serie": "20345678",
        "nbiot": True,
    },
}


async def setup_integration(
    hass: HomeAssistant, datos: dict[str, Any] | None = None
) -> MockConfigEntry:
    """Monta la integración con un coordinator que no toca la red."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=CONTRACT_ID,
        title="EMASESA 0012345678",
        data={
            CONF_USERNAME: USERNAME,
            CONF_PASSWORD: PASSWORD,
            CONF_DEVICE_ID: "dispositivo-1",
            CONF_CONTRACT_ID: CONTRACT_ID,
            CONF_CONTRACT_NUMBER: "0012345678",
        },
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.emasesa.coordinator.EmasesaCoordinator._async_update_data",
        return_value=DATOS if datos is None else datos,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def estado(hass: HomeAssistant, dominio: str, key: str) -> Any:
    """Estado de una entidad buscándola por su `unique_id`.

    Los `entity_id` dependen del idioma de Home Assistant en el momento del
    alta (`sensor..._meter_index` en inglés, `..._indice_del_contador` en
    español); el `unique_id` no cambia nunca.
    """
    entity_id = er.async_get(hass).async_get_entity_id(
        dominio, DOMAIN, f"{CONTRACT_ID}_{key}"
    )
    assert entity_id, f"no se ha creado ninguna entidad con la clave {key}"
    return hass.states.get(entity_id)


# --------------------------------------------------------------------------- #
# Identidad de las entidades
# --------------------------------------------------------------------------- #
# Estos identificadores son PÚBLICOS: ya están en las instalaciones de quien
# tiene la integración. Cambiar uno equivale a borrarle su histórico, así que
# esta lista sólo puede crecer.
UNIQUE_IDS_PUBLICADOS = {
    f"{CONTRACT_ID}_indice",
    f"{CONTRACT_ID}_consumo_hoy",
    f"{CONTRACT_ID}_coste_periodo",
    f"{CONTRACT_ID}_precio_m3",
    f"{CONTRACT_ID}_ultima_factura",
    f"{CONTRACT_ID}_deuda_pendiente",
    f"{CONTRACT_ID}_consumo_medio",
    f"{CONTRACT_ID}_dias_proxima_factura",
    f"{CONTRACT_ID}_embalses",
    f"{CONTRACT_ID}_embalse_aracena",
    f"{CONTRACT_ID}_embalse_la_minilla",
    f"{CONTRACT_ID}_posible_fuga",
    f"{CONTRACT_ID}_averia_contador",
    f"{CONTRACT_ID}_incidencia_pendiente",
    f"{CONTRACT_ID}_incidencia_cercana",
}


async def test_los_unique_id_no_cambian(hass: HomeAssistant) -> None:
    """Blindaje contra refactores: los ids de entidad son un contrato."""
    entry = await setup_integration(hass)
    registro = er.async_get(hass)
    entidades = er.async_entries_for_config_entry(registro, entry.entry_id)

    assert {e.unique_id for e in entidades} == UNIQUE_IDS_PUBLICADOS


async def test_se_crean_todas_las_entidades(hass: HomeAssistant) -> None:
    entry = await setup_integration(hass)
    registro = er.async_get(hass)
    entidades = er.async_entries_for_config_entry(registro, entry.entry_id)

    assert sum(e.domain == "sensor" for e in entidades) == 11
    assert sum(e.domain == "binary_sensor" for e in entidades) == 4


# --------------------------------------------------------------------------- #
# Valores publicados
# --------------------------------------------------------------------------- #
async def test_sensores_principales(hass: HomeAssistant) -> None:
    await setup_integration(hass)

    indice = estado(hass, "sensor", "indice")
    assert indice is not None
    assert indice.state == "443.601"
    assert indice.attributes["indice_litros"] == 443601
    assert indice.attributes["numero_serie"] == "20345678"
    assert indice.attributes["telelectura_nbiot"] is True
    # El id de la estadística de largo plazo, para el panel de Energía.
    assert indice.attributes["estadistica_historica"] == f"{DOMAIN}:{CONTRACT_ID}_water"
    assert indice.attributes["unit_of_measurement"] == "m³"
    assert indice.attributes["state_class"] == "total_increasing"

    hoy = estado(hass, "sensor", "consumo_hoy")
    assert hoy.state == "16"
    assert hoy.attributes["consumo_nocturno_litros"] == 1
    # Es un valor diario: sin state_class, para que el recorder no lo sume.
    assert "state_class" not in hoy.attributes

    assert estado(hass, "sensor", "coste_periodo").state == ("23.47")
    assert estado(hass, "sensor", "precio_m3").state == ("2.6078")
    assert estado(hass, "sensor", "ultima_factura").state == "41.22"
    assert estado(hass, "sensor", "deuda_pendiente").state == "0.0"
    assert estado(hass, "sensor", "consumo_medio").state == ("187.0")


async def test_ultima_factura_lleva_los_datos_del_recibo(
    hass: HomeAssistant,
) -> None:
    await setup_integration(hass)
    factura = estado(hass, "sensor", "ultima_factura")

    assert factura.attributes["numero"] == "F2026-000123"
    assert factura.attributes["consumo_m3"] == 17.0
    assert factura.attributes["dias_facturados"] == 61
    assert factura.attributes["periodo_hasta"] == "2026-06-13"


@pytest.mark.freeze_time("2026-08-05 10:00:00+02:00")
async def test_dias_hasta_la_proxima_factura(hass: HomeAssistant) -> None:
    """Del 5 al 15 de agosto van 10 días."""
    await setup_integration(hass)
    assert estado(hass, "sensor", "dias_proxima_factura").state == "10"


@pytest.mark.freeze_time("2026-09-01 10:00:00+02:00")
async def test_la_proxima_factura_no_va_hacia_atras(hass: HomeAssistant) -> None:
    """Si la fecha ya pasó se muestra 0, no un número negativo."""
    await setup_integration(hass)
    assert estado(hass, "sensor", "dias_proxima_factura").state == "0"


async def test_fecha_de_factura_ilegible(hass: HomeAssistant) -> None:
    """Una fecha con formato inesperado deja el sensor en desconocido."""
    datos = {**DATOS, "periodo_facturacion": {"proxima_factura": "pronto"}}
    await setup_integration(hass, datos)
    assert estado(hass, "sensor", "dias_proxima_factura").state == (STATE_UNKNOWN)


# --------------------------------------------------------------------------- #
# Embalses
# --------------------------------------------------------------------------- #
async def _habilitar_embalses(
    hass: HomeAssistant, entry: MockConfigEntry, datos: dict[str, Any] | None = None
) -> None:
    """Activa los sensores por embalse, que vienen desactivados de fábrica."""
    registro = er.async_get(hass)
    for e in er.async_entries_for_config_entry(registro, entry.entry_id):
        if "_embalse_" in e.unique_id:
            registro.async_update_entity(e.entity_id, disabled_by=None)

    with patch(
        "custom_components.emasesa.coordinator.EmasesaCoordinator._async_update_data",
        return_value=DATOS if datos is None else datos,
    ):
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()


async def test_el_sensor_conjunto_lleva_el_desglose_en_atributos(
    hass: HomeAssistant,
) -> None:
    """Para ver a qué embalses se refiere sin activar seis sensores más."""
    await setup_integration(hass)

    conjunto = estado(hass, "sensor", "embalses")
    assert conjunto.state == "80.4"
    assert conjunto.attributes["capacidad_hm3"] == 223.4
    assert conjunto.attributes["volumen_hm3"] == 179.6
    assert conjunto.attributes["fecha"] == "2026-08-01"

    desglose = conjunto.attributes["por_embalse"]
    assert [e["nombre"] for e in desglose] == ["Aracena", "La Minilla"]
    assert desglose[0]["porc_llenado"] == 76.2


async def test_los_sensores_por_embalse_vienen_desactivados(
    hass: HomeAssistant,
) -> None:
    """Seis sensores más para un dato que ya está en los atributos estorban.

    Se crean igual, para que quien quiera histórico de un embalse concreto
    sólo tenga que activarlo.
    """
    entry = await setup_integration(hass)
    registro = er.async_get(hass)

    por_embalse = [
        e
        for e in er.async_entries_for_config_entry(registro, entry.entry_id)
        if "_embalse_" in e.unique_id
    ]
    assert len(por_embalse) == 2
    assert all(
        e.disabled_by is er.RegistryEntryDisabler.INTEGRATION for e in por_embalse
    )
    # Y por tanto no publican estado mientras no se activen.
    assert hass.states.get(por_embalse[0].entity_id) is None

    # El sensor conjunto, en cambio, sí está activo desde el principio.
    assert estado(hass, "sensor", "embalses").state == "80.4"


async def test_al_activarlos_publican_su_nivel(hass: HomeAssistant) -> None:
    entry = await setup_integration(hass)
    await _habilitar_embalses(hass, entry)

    aracena = estado(hass, "sensor", "embalse_aracena")
    assert aracena.state == "76.2"
    assert aracena.attributes["embalse"] == "Aracena"
    assert aracena.attributes["volumen_hm3"] == 96.4
    assert aracena.attributes["fecha"] == "2026-08-01"

    assert estado(hass, "sensor", "embalse_la_minilla").state == "91.3"


async def test_no_hay_subdispositivo_de_embalses(hass: HomeAssistant) -> None:
    """Todo cuelga del dispositivo del contrato: un cacharro, no dos.

    Hasta la 0.6.0 los embalses colgaban de un sub-dispositivo propio. Separaba
    bien lo de la ciudad de lo del usuario, pero a cambio metía un dispositivo
    de más en la lista para seis sensores que casi nadie mira.
    """
    await setup_integration(hass)
    registro = dr.async_get(hass)

    contrato = registro.async_get_device(identifiers={(DOMAIN, CONTRACT_ID)})
    assert contrato is not None
    assert (
        registro.async_get_device(identifiers={(DOMAIN, f"{CONTRACT_ID}_embalses")})
        is None
    )

    # Los sensores de embalses son del dispositivo del contrato.
    entidad = er.async_get(hass).async_get(
        er.async_get(hass).async_get_entity_id(
            "sensor", DOMAIN, f"{CONTRACT_ID}_embalses"
        )
    )
    assert entidad.device_id == contrato.id

    # Y ese dispositivo lleva los datos reales del contador.
    assert contrato.manufacturer == "Contazara"
    assert contrato.model == "CZ2000"
    assert contrato.serial_number == "20345678"


async def test_se_retira_el_subdispositivo_de_versiones_anteriores(
    hass: HomeAssistant,
) -> None:
    """Quien venga de la 0.5.1 tiene el dispositivo vacío: hay que quitarlo."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=CONTRACT_ID,
        data={
            CONF_USERNAME: USERNAME,
            CONF_PASSWORD: PASSWORD,
            CONF_DEVICE_ID: "dispositivo-1",
            CONF_CONTRACT_ID: CONTRACT_ID,
            CONF_CONTRACT_NUMBER: "0012345678",
        },
    )
    entry.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{CONTRACT_ID}_embalses")},
        name="Embalses EMASESA 0012345678",
    )

    with patch(
        "custom_components.emasesa.coordinator.EmasesaCoordinator._async_update_data",
        return_value=DATOS,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert (
        dr.async_get(hass).async_get_device(
            identifiers={(DOMAIN, f"{CONTRACT_ID}_embalses")}
        )
        is None
    )


async def test_no_se_retira_el_subdispositivo_si_le_quedan_entidades(
    hass: HomeAssistant,
) -> None:
    """Ante la duda, un dispositivo de más antes que borrarle el histórico."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=CONTRACT_ID,
        data={
            CONF_USERNAME: USERNAME,
            CONF_PASSWORD: PASSWORD,
            CONF_DEVICE_ID: "dispositivo-1",
            CONF_CONTRACT_ID: CONTRACT_ID,
            CONF_CONTRACT_NUMBER: "0012345678",
        },
    )
    entry.add_to_hass(hass)
    viejo = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{CONTRACT_ID}_embalses")},
        name="Embalses EMASESA 0012345678",
    )
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{CONTRACT_ID}_algo_que_no_migro",
        config_entry=entry,
        device_id=viejo.id,
    )

    with patch(
        "custom_components.emasesa.coordinator.EmasesaCoordinator._async_update_data",
        return_value=DATOS,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert (
        dr.async_get(hass).async_get_device(
            identifiers={(DOMAIN, f"{CONTRACT_ID}_embalses")}
        )
        is not None
    )


async def test_sin_embalses_no_se_crean_sensores_individuales(
    hass: HomeAssistant,
) -> None:
    """La lista la decide la API: si no la manda, no hay sensores por embalse."""
    datos = {**DATOS, "embalses": {}}
    entry = await setup_integration(hass, datos)

    registro = er.async_get(hass)
    ids = {
        e.unique_id for e in er.async_entries_for_config_entry(registro, entry.entry_id)
    }
    assert not [i for i in ids if "_embalse_" in i]
    # El sensor conjunto sí existe, aunque esté en desconocido.
    assert f"{CONTRACT_ID}_embalses" in ids


async def test_embalse_que_desaparece_queda_no_disponible(
    hass: HomeAssistant,
) -> None:
    """Si la API deja de mandar un embalse, su sensor no inventa un valor."""
    entry = await setup_integration(hass)
    await _habilitar_embalses(hass, entry)
    assert estado(hass, "sensor", "embalse_aracena").state == "76.2"

    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.async_set_updated_data(
        {**DATOS, "embalses": {**DATOS["embalses"], "por_embalse": []}}
    )
    await hass.async_block_till_done()

    assert estado(hass, "sensor", "embalse_aracena").state == ("unavailable")


# --------------------------------------------------------------------------- #
# Sensores binarios
# --------------------------------------------------------------------------- #
async def test_binarios(hass: HomeAssistant) -> None:
    await setup_integration(hass)

    fuga = estado(hass, "binary_sensor", "posible_fuga")
    assert fuga.state == STATE_OFF
    assert fuga.attributes["noches_analizadas"] == 3

    assert estado(hass, "binary_sensor", "averia_contador").state == (STATE_OFF)
    assert estado(hass, "binary_sensor", "incidencia_pendiente").state == STATE_OFF

    cercana = estado(hass, "binary_sensor", "incidencia_cercana")
    assert cercana.state == STATE_ON
    assert cercana.attributes["numero"] == 1
    assert cercana.attributes["distancia_m"] == 320
    assert cercana.attributes["total_ciudad"] == 12


async def test_fuga_sin_analizar_queda_en_desconocido(hass: HomeAssistant) -> None:
    """Sin telelectura horaria no se puede afirmar que NO hay fuga.

    Decir "sin fuga" sin haber mirado ninguna noche es afirmar algo que no se
    sabe, y ese sensor es el que la gente usa para automatizar avisos.
    """
    datos = {**DATOS, "fuga": {"analizado": False, "noches": 0}}
    await setup_integration(hass, datos)

    fuga = estado(hass, "binary_sensor", "posible_fuga")
    assert fuga.state == STATE_UNKNOWN
    assert fuga.attributes["analizado"] is False


async def test_fuga_detectada(hass: HomeAssistant) -> None:
    datos = {
        **DATOS,
        "fuga": {
            "analizado": True,
            "detectada": True,
            "noches": 3,
            "min_l_h": 2,
            "desde": "2026-07-29",
        },
    }
    await setup_integration(hass, datos)

    fuga = estado(hass, "binary_sensor", "posible_fuga")
    assert fuga.state == STATE_ON
    assert fuga.attributes["consumo_minimo_nocturno_l"] == 2


async def test_averia_del_contador(hass: HomeAssistant) -> None:
    datos = {**DATOS, "averia_estimacion": True, "incidencia_pendiente": True}
    await setup_integration(hass, datos)

    assert estado(hass, "binary_sensor", "averia_contador").state == (STATE_ON)
    assert estado(hass, "binary_sensor", "incidencia_pendiente").state == STATE_ON


# --------------------------------------------------------------------------- #
# Datos incompletos
# --------------------------------------------------------------------------- #
async def test_payload_vacio_no_rompe_ninguna_entidad(hass: HomeAssistant) -> None:
    """Con un contrato sin telelectura casi todo llega vacío.

    Ninguna entidad puede petar por eso: como mucho quedan en "desconocido".
    """
    await setup_integration(hass, {"contract_id": CONTRACT_ID})

    for state in hass.states.async_all():
        assert state.state in (STATE_UNKNOWN, STATE_OFF), state.entity_id


async def test_descarga_de_la_entrada(hass: HomeAssistant) -> None:
    entry = await setup_integration(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.entry_id not in hass.data.get(DOMAIN, {})
