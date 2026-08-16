"""Tests del coordinador (`custom_components/emasesa/coordinator.py`).

Se prueba la lógica pura de `_import_statistics` sin levantar Home Assistant:
se construye el coordinador con `__new__` (saltándose `DataUpdateCoordinator.
__init__`, que sí necesita un `hass` real) y se parchean las dos únicas puertas
al recorder: `async_add_external_statistics` y `_last_stat`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip(
    "homeassistant.components.recorder",
    reason="Home Assistant no está instalado (pip install -r requirements-test.txt)",
)

from custom_components.emasesa import coordinator as coordinator_module
from custom_components.emasesa.const import (
    DOMAIN,
    INITIAL_BACKFILL_DAYS,
    LITERS_PER_M3,
    MAX_BACKFILL_DAYS,
    SCAN_INTERVAL,
    SCAN_INTERVAL_ESPERA,
    UPDATE_BACKFILL_DAYS,
)
from custom_components.emasesa.coordinator import EmasesaCoordinator
from homeassistant.util import dt as dt_util

from .conftest import (
    CONTRACT_ID,
    build_day,
    day_2026_07_30,
    day_2026_07_31,
    day_dst_octubre,
)

MADRID = ZoneInfo("Europe/Madrid")
PRECIO_ALTO = 2.5  # €/m³ al principio del ciclo (cuota fija muy repartida)
PRECIO_BAJO = 1.8  # €/m³ más adelante: el precio efectivo BAJA


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def stats_calls(monkeypatch) -> list[tuple[dict, list[dict]]]:
    """Captura las llamadas a `async_add_external_statistics`."""
    calls: list[tuple[dict, list[dict]]] = []

    def _fake(hass, metadata, stats):
        calls.append((dict(metadata), [dict(s) for s in stats]))

    monkeypatch.setattr(coordinator_module, "async_add_external_statistics", _fake)
    return calls


@pytest.fixture
def coordinator() -> EmasesaCoordinator:
    """Coordinador sin `hass`: sólo se ejercita la lógica de conversión."""
    coord = EmasesaCoordinator.__new__(EmasesaCoordinator)
    # hass mínimo: el recorder está parcheado, pero la caché de datos
    # globales sí usa hass.data.
    coord.hass = type("H", (), {"data": {}})()
    coord.client = AsyncMock()
    coord.contract_id = CONTRACT_ID
    coord.statistic_id = f"{DOMAIN}:{CONTRACT_ID}_water"
    coord.cost_statistic_id = f"{DOMAIN}:{CONTRACT_ID}_water_cost"
    coord._tz = None
    coord._warned_no_hourly = False
    coord._last_import_empty = False
    coord.incident_radius_m = 1000
    coord.latitude = None
    coord.longitude = None
    coord._last_stat = AsyncMock(return_value=None)
    # Sondeo adaptativo, tal y como lo deja __init__.
    coord._intervalo_largo = SCAN_INTERVAL
    coord._intervalo_corto = SCAN_INTERVAL_ESPERA
    coord._ultima_fecha_dato = None
    coord.update_interval = SCAN_INTERVAL
    return coord


def _by_id(calls, statistic_id) -> list[dict[str, Any]]:
    """Devuelve las filas importadas para una estadística concreta."""
    for metadata, stats in calls:
        if metadata["statistic_id"] == statistic_id:
            return stats
    return []


def _es_monotona(valores) -> bool:
    return all(b >= a for a, b in zip(valores, valores[1:], strict=False))


# --------------------------------------------------------------------------- #
# Consumo: 'sum' monotónica a partir del índice del contador
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_import_statistics_sum_monotonica_en_m3(
    coordinator, stats_calls, sample_day
):
    await coordinator._import_statistics([sample_day])

    assert len(stats_calls) == 1, "sin precio no debe importarse coste"
    metadata, stats = stats_calls[0]

    assert metadata["statistic_id"] == f"{DOMAIN}:{CONTRACT_ID}_water"
    assert metadata["source"] == DOMAIN
    assert metadata["has_sum"] is True
    assert metadata["unit_of_measurement"] == "m³"

    assert len(stats) == 24

    # state = lectura real de esa hora en m³; sum = máximo acumulado.
    detalle = sample_day["detalle"]
    assert [s["state"] for s in stats] == [h["indice"] / LITERS_PER_M3 for h in detalle]
    assert stats[0]["sum"] == pytest.approx(443.585)
    assert stats[-1]["sum"] == pytest.approx(443.601)

    sums = [s["sum"] for s in stats]
    assert _es_monotona(sums)
    # El consumo del día que deduce el panel de Energía = 16 L.
    assert (sums[-1] - sums[0]) * LITERS_PER_M3 == pytest.approx(sample_day["consumo"])


@pytest.mark.asyncio
async def test_import_statistics_convierte_hora_local_a_utc(
    coordinator, stats_calls, sample_day
):
    """Julio en Madrid es UTC+2, y el índice de la hora H es la lectura al
    comienzo de H, así que la fila se guarda una hora antes: la hora local 00
    del 31 acaba en las 21:00 UTC del día 30."""
    await coordinator._import_statistics([sample_day])
    _, stats = stats_calls[0]

    starts = [s["start"] for s in stats]
    assert starts[0] == datetime(2026, 7, 30, 21, 0, tzinfo=UTC)
    assert starts[-1] == datetime(2026, 7, 31, 20, 0, tzinfo=UTC)
    assert all(s.tzinfo is not None for s in starts)
    assert all(
        b - a == timedelta(hours=1) for a, b in zip(starts, starts[1:], strict=False)
    )


@pytest.mark.asyncio
async def test_import_statistics_indice_que_retrocede_no_rompe_la_suma(
    coordinator, stats_calls
):
    """Una lectura estimada puede retroceder; la 'sum' se queda plana (clamp)."""
    dia = day_2026_07_31()
    dia["detalle"][10]["indice"] = 443000  # retroceso artificial

    await coordinator._import_statistics([dia])
    _, stats = stats_calls[0]

    # El 'state' conserva la lectura real (bajada incluida)...
    assert stats[10]["state"] == pytest.approx(443.0)
    assert stats[10]["state"] < stats[9]["state"]
    # ...pero la 'sum' nunca baja, así que Energía no ve consumos negativos.
    assert _es_monotona([s["sum"] for s in stats])
    assert stats[10]["sum"] == stats[9]["sum"]


@pytest.mark.asyncio
async def test_import_statistics_ordena_los_dias(coordinator, stats_calls):
    """Aunque la API devuelva los días desordenados, los puntos salen en orden."""
    await coordinator._import_statistics([day_2026_07_31(), day_2026_07_30()])
    _, stats = stats_calls[0]

    starts = [s["start"] for s in stats]
    assert len(stats) == 48
    assert starts == sorted(starts)
    assert _es_monotona([s["sum"] for s in stats])
    assert stats[0]["sum"] == pytest.approx(443.559)
    assert stats[-1]["sum"] == pytest.approx(443.601)


@pytest.mark.asyncio
async def test_import_statistics_ignora_entradas_incompletas(coordinator, stats_calls):
    dia = day_2026_07_31()
    dia["detalle"][3]["indice"] = None  # hueco de telelectura
    dia["detalle"][4].pop("hora")
    dias = [
        dia,
        {"fecha": "2026-07-29"},  # sin detalle
        {"detalle": [{"hora": "00", "indice": 1}]},  # sin fecha
        {"fecha": "2026-07-28", "detalle": "KO"},  # detalle no es lista
    ]

    await coordinator._import_statistics(dias)
    _, stats = stats_calls[0]
    assert len(stats) == 22


@pytest.mark.asyncio
async def test_import_statistics_sin_dias_no_hace_nada(coordinator, stats_calls):
    await coordinator._import_statistics([], PRECIO_ALTO)
    assert stats_calls == []
    coordinator._last_stat.assert_not_awaited()


@pytest.mark.asyncio
async def test_import_statistics_sin_puntos_no_hace_nada(coordinator, stats_calls):
    await coordinator._import_statistics([{"fecha": "2026-07-31", "detalle": []}])
    assert stats_calls == []


# --------------------------------------------------------------------------- #
# Coste: acumulado por incrementos, nunca decreciente
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_coste_se_acumula_por_incrementos(coordinator, stats_calls, sample_day):
    await coordinator._import_statistics([sample_day], PRECIO_ALTO)

    coste = _by_id(stats_calls, f"{DOMAIN}:{CONTRACT_ID}_water_cost")
    metadata = next(
        m for m, _ in stats_calls if m["statistic_id"].endswith("_water_cost")
    )
    assert metadata["unit_of_measurement"] == "EUR"
    assert metadata["has_sum"] is True

    # El primer punto sólo sirve de referencia para el delta: 24 horas -> 23 filas.
    assert len(coste) == 23

    sums = [s["sum"] for s in coste]
    assert _es_monotona(sums)
    assert sums[0] >= 0.0
    # 16 L = 0,016 m³ a 2,50 €/m³ = 0,04 €.
    consumo_m3 = sample_day["consumo"] / LITERS_PER_M3
    assert sums[-1] == pytest.approx(consumo_m3 * PRECIO_ALTO, abs=1e-4)
    # 'state' y 'sum' coinciden (es un total acumulado).
    assert all(s["state"] == s["sum"] for s in coste)


@pytest.mark.asyncio
async def test_sin_precio_no_hay_estadistica_de_coste(
    coordinator, stats_calls, sample_day
):
    await coordinator._import_statistics([sample_day], None)
    assert _by_id(stats_calls, f"{DOMAIN}:{CONTRACT_ID}_water_cost") == []
    coordinator._last_stat.assert_not_awaited()


@pytest.mark.asyncio
async def test_coste_nunca_decrece_al_reimportar_con_precio_menor(
    coordinator, stats_calls
):
    """Regresión del bug de falsos resets del contador de coste.

    El precio efectivo €/m³ BAJA dentro del ciclo (la cuota fija se reparte
    entre más m³). Si el coste se calculase como `indice_absoluto × precio`,
    al reimportar días ya escritos la 'sum' bajaría y el panel de Energía lo
    interpretaría como un reset del contador. Debe acumularse por incrementos.
    """
    # --- primera importación ------------------------------------------------
    await coordinator._import_statistics(
        [day_2026_07_30(), day_2026_07_31()], PRECIO_ALTO
    )
    coste_1 = _by_id(stats_calls, f"{DOMAIN}:{CONTRACT_ID}_water_cost")
    ultimo = coste_1[-1]
    ultimo_sum = ultimo["sum"]
    ultimo_ts = ultimo["start"].timestamp()

    assert _es_monotona([s["sum"] for s in coste_1])
    assert ultimo_sum == pytest.approx(0.105, abs=1e-4)  # 42 L a 2,50 €/m³

    # --- segunda importación: mismos días + uno nuevo, precio MÁS BAJO ------
    stats_calls.clear()
    coordinator._last_stat = AsyncMock(
        return_value={"sum": ultimo_sum, "start": ultimo_ts}
    )
    dia_nuevo = build_day(
        "2026-08-01",
        443601,
        [0, 0, 0, 0, 0, 0, 2, 0],  # día en curso, parcial
    )
    await coordinator._import_statistics(
        [day_2026_07_30(), day_2026_07_31(), dia_nuevo], PRECIO_BAJO
    )

    coste_2 = _by_id(stats_calls, f"{DOMAIN}:{CONTRACT_ID}_water_cost")
    assert coste_2, "debería haber filas nuevas de coste"

    # 1) No se reescribe nada ya contabilizado.
    assert all(s["start"].timestamp() > ultimo_ts for s in coste_2)
    # 2) La serie continúa desde el último valor guardado y nunca decrece.
    assert coste_2[0]["sum"] >= ultimo_sum
    assert _es_monotona([ultimo_sum] + [s["sum"] for s in coste_2])
    # 3) El delta del primer punto nuevo se mide contra el último índice del
    #    día anterior, no contra cero (no hay salto artificial).
    assert coste_2[0]["sum"] == pytest.approx(ultimo_sum, abs=1e-4)
    # 4) El consumo nuevo (2 L) se cobra al precio nuevo.
    assert coste_2[-1]["sum"] == pytest.approx(
        ultimo_sum + (2 / LITERS_PER_M3) * PRECIO_BAJO, abs=1e-4
    )

    # Demostración de por qué hacía falta: con el enfoque "índice × precio"
    # la suma habría CAÍDO de golpe al bajar el precio -> falso reset.
    indice_m3 = 443603 / LITERS_PER_M3
    assert indice_m3 * PRECIO_BAJO < indice_m3 * PRECIO_ALTO

    # El consumo, en cambio, sí se reimporta entero (es idempotente).
    consumo_2 = _by_id(stats_calls, f"{DOMAIN}:{CONTRACT_ID}_water")
    assert len(consumo_2) == 24 + 24 + 8


@pytest.mark.asyncio
async def test_coste_solo_consulta_la_estadistica_de_coste(
    coordinator, stats_calls, sample_day
):
    await coordinator._import_statistics([sample_day], PRECIO_ALTO)
    coordinator._last_stat.assert_awaited_once_with(
        f"{DOMAIN}:{CONTRACT_ID}_water_cost"
    )


@pytest.mark.asyncio
async def test_coste_no_baja_aunque_el_indice_retroceda(coordinator, stats_calls):
    """Un retroceso del índice no puede generar coste negativo."""
    dia = day_2026_07_31()
    dia["detalle"][12]["indice"] = 443000

    await coordinator._import_statistics([dia], PRECIO_ALTO)
    coste = _by_id(stats_calls, f"{DOMAIN}:{CONTRACT_ID}_water_cost")

    assert _es_monotona([s["sum"] for s in coste])
    assert all(s["sum"] >= 0 for s in coste)


# --------------------------------------------------------------------------- #
# Cambio de horario de octubre: la hora local 02 aparece dos veces
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_dst_octubre_hora_02_duplicada_no_colisiona(coordinator, stats_calls):
    dia = day_dst_octubre()
    assert [h["hora"] for h in dia["detalle"]].count("02") == 2

    await coordinator._import_statistics([dia])
    _, stats = stats_calls[0]

    starts = [s["start"] for s in stats]
    assert len(stats) == 25, "el 25/10/2026 tiene 25 horas locales"
    assert len(set(starts)) == 25, "las dos horas 02 deben caer en instantes distintos"

    # 02 CEST -> 00:00 UTC y 02 CET (fold=1) -> 01:00 UTC; menos la hora que se
    # resta porque el índice es la lectura al comienzo de la hora.
    assert starts[2] == datetime(2026, 10, 24, 23, 0, tzinfo=UTC)
    assert starts[3] == datetime(2026, 10, 25, 0, 0, tzinfo=UTC)
    assert starts[3] - starts[2] == timedelta(hours=1)

    # Día completo sin huecos ni solapes.
    assert starts == sorted(starts)
    assert all(
        b - a == timedelta(hours=1) for a, b in zip(starts, starts[1:], strict=False)
    )
    assert _es_monotona([s["sum"] for s in stats])


def test_dst_sin_fold_las_dos_horas_02_colisionarian():
    """Control: sin `fold=1` ambas lecturas caerían en el mismo instante UTC."""
    naive = datetime(2026, 10, 25, 2, 0)
    sin_fold = naive.replace(tzinfo=MADRID).astimezone(UTC)
    con_fold = naive.replace(tzinfo=MADRID, fold=1).astimezone(UTC)
    assert sin_fold != con_fold
    assert con_fold - sin_fold == timedelta(hours=1)


@pytest.mark.asyncio
async def test_dst_octubre_coste_tambien_es_monotono(coordinator, stats_calls):
    await coordinator._import_statistics([day_dst_octubre()], PRECIO_ALTO)
    coste = _by_id(stats_calls, f"{DOMAIN}:{CONTRACT_ID}_water_cost")

    assert len(coste) == 24  # 25 puntos - el primero (referencia)
    assert len({s["start"] for s in coste}) == 24
    assert _es_monotona([s["sum"] for s in coste])


# --------------------------------------------------------------------------- #
# Ventana de histórico
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_days_to_backfill_sin_estadisticas_previas(coordinator):
    coordinator._last_stat = AsyncMock(return_value=None)
    assert await coordinator._days_to_backfill() == INITIAL_BACKFILL_DAYS


@pytest.mark.asyncio
async def test_days_to_backfill_hueco_corto_usa_la_ventana_normal(coordinator):
    """Con datos recientes no se reimportan 60 días en cada arranque de HA."""
    hace_dos_dias = dt_util.utcnow() - timedelta(days=2)
    coordinator._last_stat = AsyncMock(
        return_value={"start": hace_dos_dias.timestamp(), "sum": 443.601}
    )
    assert await coordinator._days_to_backfill() == UPDATE_BACKFILL_DAYS
    coordinator._last_stat.assert_awaited_once_with(f"{DOMAIN}:{CONTRACT_ID}_water")


@pytest.mark.asyncio
async def test_days_to_backfill_hueco_largo_se_topa_en_el_maximo(coordinator):
    """Si HA o la API llevan años caídos, no se pide un histórico infinito."""
    hace_mucho = dt_util.utcnow() - timedelta(days=900)
    coordinator._last_stat = AsyncMock(
        return_value={"start": hace_mucho.timestamp(), "sum": 100.0}
    )
    assert await coordinator._days_to_backfill() == MAX_BACKFILL_DAYS


@pytest.mark.asyncio
async def test_days_to_backfill_hueco_medio(coordinator):
    hace_diez_dias = dt_util.utcnow() - timedelta(days=10)
    coordinator._last_stat = AsyncMock(
        return_value={"start": hace_diez_dias.timestamp(), "sum": 443.601}
    )
    dias = await coordinator._days_to_backfill()
    assert dias == 11  # 10 días de hueco + 1 de margen
    assert UPDATE_BACKFILL_DAYS < dias < MAX_BACKFILL_DAYS


@pytest.mark.asyncio
async def test_fetch_history_chunked_trocea_en_30_dias(coordinator):
    coordinator.client.get_consumption = AsyncMock(return_value=[])

    await coordinator._fetch_history_chunked(
        datetime(2026, 6, 1).date(), datetime(2026, 7, 31).date()
    )

    rangos = [
        (c.args[1], c.args[2])
        for c in coordinator.client.get_consumption.await_args_list
    ]
    assert rangos == [
        (datetime(2026, 6, 1).date(), datetime(2026, 7, 1).date()),
        (datetime(2026, 7, 2).date(), datetime(2026, 7, 31).date()),
    ]
    # Tramos contiguos y sin solape.
    assert rangos[1][0] - rangos[0][1] == timedelta(days=1)
    assert all(
        c.args[0] == CONTRACT_ID
        for c in coordinator.client.get_consumption.await_args_list
    )


@pytest.mark.asyncio
async def test_embalses_expone_uno_por_embalse(coordinator):
    """Cada embalse sale por separado, no solo escondido en los atributos."""
    coordinator.client.get_reservoirs = AsyncMock(
        return_value={
            "fecha": "02/08/2026",
            # Los seis embalses reales del sistema de abastecimiento de Sevilla.
            "embalses": [
                {
                    "embalse": "Aracena",
                    "vol_embalsado": 107.0,
                    "capacidad": 128.65,
                    "porc_llenado": 83.2,
                },
                {
                    "embalse": "Zufre",
                    "vol_embalsado": 150.09,
                    "capacidad": 175.27,
                    "porc_llenado": 85.6,
                },
                {
                    "embalse": "Minilla",
                    "vol_embalsado": 32.62,
                    "capacidad": 57.8,
                    "porc_llenado": 56.4,
                },
                {
                    "embalse": "Gergal",
                    "vol_embalsado": 21.7,
                    "capacidad": 35.05,
                    "porc_llenado": 61.9,
                },
                {
                    "embalse": "Cala",
                    "vol_embalsado": 39.4,
                    "capacidad": 57.52,
                    "porc_llenado": 68.5,
                },
                {
                    "embalse": "Melonares",
                    "vol_embalsado": 164.51,
                    "capacidad": 186.87,
                    "porc_llenado": 88.2,
                },
            ],
        }
    )
    datos = await coordinator._fetch_reservoirs()

    porc = {e["nombre"]: e["porc_llenado"] for e in datos["por_embalse"]}
    assert porc == {
        "Aracena": 83.2,
        "Zufre": 85.6,
        "Minilla": 56.4,
        "Gergal": 61.9,
        "Cala": 68.5,
        "Melonares": 88.2,
    }
    # cada uno conserva volumen y capacidad para sus atributos
    aracena = next(e for e in datos["por_embalse"] if e["nombre"] == "Aracena")
    assert aracena["vol_embalsado_hm3"] == 107.0
    assert aracena["capacidad_hm3"] == 128.65
    # y el conjunto sigue siendo la media ponderada real
    assert datos["porc_llenado"] == pytest.approx(80.4, abs=0.1)


@pytest.mark.asyncio
async def test_backfill_converge_si_el_import_no_produjo_puntos(coordinator):
    """Sin telelectura no hay estadísticas nunca: no se piden 60 días por ciclo.

    Regresión: get_last_statistics siempre devolvía vacío para un contador sin
    NB-IoT, así que se re-descargaba el histórico completo en cada ciclo contra
    la API privada, indefinidamente.
    """
    coordinator._last_stat = AsyncMock(return_value=None)

    # Primer ciclo: aún no se sabe nada, se intenta el histórico completo.
    assert await coordinator._days_to_backfill() == INITIAL_BACKFILL_DAYS

    # Un import que no genera ni un punto marca la bandera...
    await coordinator._import_statistics([{"fecha": "2026-07-31", "detalle": []}])
    assert coordinator._last_import_empty is True

    # ...y a partir de ahí la ventana se acorta.
    assert await coordinator._days_to_backfill() == UPDATE_BACKFILL_DAYS


@pytest.mark.asyncio
async def test_dia_sin_detalle_horario_genera_punto_diario(coordinator, stats_calls):
    """Con lectura diaria pero sin desglose horario, Energía tiene algo."""
    dias = [
        {"fecha": "2026-07-30", "indice": 443559},
        {"fecha": "2026-07-31", "indice": 443601},
    ]
    await coordinator._import_statistics(dias)

    _, stats = stats_calls[0]
    assert len(stats) == 2, "un punto por día"
    # 23:00 local menos la hora de desplazamiento -> 22:00 local = 20:00 UTC
    assert stats[0]["start"] == datetime(2026, 7, 30, 20, 0, tzinfo=UTC)
    assert stats[1]["start"] == datetime(2026, 7, 31, 20, 0, tzinfo=UTC)
    assert stats[-1]["sum"] == pytest.approx(443.601)
    assert _es_monotona([s["sum"] for s in stats])
    # y al haber producido puntos, el backfill no se degrada
    assert coordinator._last_import_empty is False


@pytest.mark.asyncio
async def test_incidencias_usan_la_ubicacion_del_contrato(coordinator):
    """Con un segundo contrato, las incidencias son las de ESE suministro."""
    # Actuación junto a la Giralda; la "casa" de HA está lejos.
    coordinator.client.get_network_actions = AsyncMock(
        return_value=[
            {
                "categoria": "Salidero en acera",
                "direccion": "centro",
                "inicio": "30/07/2026 12:19",
                "latitud": 37.3861,
                "longitud": -5.9926,
            },
        ]
    )
    coordinator.incident_radius_m = 1000

    class _Cfg:
        latitude, longitude = 37.4031, -5.9832  # ubicación de Home Assistant

    coordinator.hass = type("H", (), {"config": _Cfg, "data": {}})()

    # Sin ubicación propia usa la de HA: la actuación queda fuera del radio.
    coordinator.latitude = coordinator.longitude = None
    assert (await coordinator._fetch_nearby_incidents())["cercanas"] == []

    # Con la ubicación del contrato al lado, sí la detecta.
    coordinator.latitude, coordinator.longitude = 37.3860, -5.9925
    cercanas = (await coordinator._fetch_nearby_incidents())["cercanas"]
    assert len(cercanas) == 1
    assert cercanas[0]["categoria"] == "Salidero en acera"


@pytest.mark.asyncio
async def test_incidencias_sin_ubicacion_no_revientan(coordinator):
    """Home Assistant sin coordenadas configuradas: se informa, no se falla."""
    coordinator.client.get_network_actions = AsyncMock(
        return_value=[{"latitud": 37.4, "longitud": -6.0}]
    )

    class _Cfg:
        latitude = longitude = None

    coordinator.hass = type("H", (), {"config": _Cfg, "data": {}})()
    coordinator.latitude = coordinator.longitude = None

    datos = await coordinator._fetch_nearby_incidents()
    assert datos["cercanas"] == []
    assert datos["sin_ubicacion"] is True


@pytest.mark.asyncio
async def test_datos_globales_se_piden_una_sola_vez(coordinator):
    """Embalses y red son iguales para todos los contratos: se cachean.

    Antes se pedían una vez por entrada, multiplicando llamadas a la API
    privada sin ganar nada cuando alguien tiene varios contratos.
    """
    coordinator.hass = type("H", (), {"data": {}})()
    llamadas = {"n": 0}

    async def _fake():
        llamadas["n"] += 1
        return {
            "embalses": [
                {
                    "embalse": "Cala",
                    "vol_embalsado": 39.4,
                    "capacidad": 57.52,
                    "porc_llenado": 68.5,
                }
            ]
        }

    coordinator.client.get_reservoirs = _fake

    a = await coordinator._fetch_reservoirs()
    b = await coordinator._fetch_reservoirs()
    assert llamadas["n"] == 1, "la segunda vez sale de la caché"
    assert a == b

    # Un segundo contrato comparte hass.data, así que tampoco vuelve a pedirlo.
    otro = EmasesaCoordinator.__new__(EmasesaCoordinator)
    otro.hass = coordinator.hass
    otro.client = coordinator.client
    await otro._fetch_reservoirs()
    assert llamadas["n"] == 1

    # Pasado el TTL sí se refresca.
    cache = coordinator.hass.data[DOMAIN]["_global_cache"]
    viejo, valor = cache["embalses"]
    cache["embalses"] = (viejo - coordinator_module.GLOBAL_CACHE_TTL, valor)
    await coordinator._fetch_reservoirs()
    assert llamadas["n"] == 2


def test_el_dispositivo_lleva_los_datos_del_contador():
    """Un solo dispositivo por contrato, con el contador real dentro.

    Los embalses colgaron de un sub-dispositivo propio entre la 0.5.1 y la
    0.6.0; se retiró porque metía un cacharro de más en la lista. Que no
    vuelva a haber más de un dispositivo se comprueba en `test_entities.py`.
    """
    from custom_components.emasesa.entity import build_device_info

    coord = EmasesaCoordinator.__new__(EmasesaCoordinator)
    coord.contract_id = CONTRACT_ID
    coord.data = {"meter": {"fabricante": "CONTAZARA", "modelo": "CZ4000 C1"}}
    entry = type("E", (), {"data": {"contrato_numero": "0012345678"}})()

    device = build_device_info(coord, entry)

    assert device["identifiers"] == {(DOMAIN, CONTRACT_ID)}
    assert device["name"] == "EMASESA 0012345678"
    assert device["manufacturer"] == "CONTAZARA"
    assert device["model"] == "CZ4000 C1"


# --------------------------------------------------------------------------- #
# Qué día se enseña en los sensores
# --------------------------------------------------------------------------- #
def test_se_usa_el_dia_mas_reciente_no_el_de_ultimo():
    """El endpoint /ultimo va un día por detrás del histórico horario.

    Caso real: el sensor del índice mostraba el 2 de agosto (443,603 m³)
    mientras las estadísticas ya tenían el 3 de agosto a mediodía (443,613).
    La integración enseñaba un dato más viejo que el que ella misma acababa
    de escribir en el panel de Energía.
    """
    from custom_components.emasesa.coordinator import _dia_mas_reciente

    cerrado = build_day("2026-08-02", 443601, [0] * 23 + [2])
    en_curso = build_day("2026-08-03", 443603, [0, 0, 0, 0, 9, 1])

    elegido = _dia_mas_reciente({d["fecha"]: d for d in (cerrado, en_curso)})

    assert elegido["fecha"] == "2026-08-03"
    assert elegido["indice"] == 443613


def test_un_dia_reciente_sin_lectura_no_tapa_al_anterior():
    """Un hueco de telelectura no puede dejar el sensor en desconocido."""
    from custom_components.emasesa.coordinator import _dia_mas_reciente

    bueno = build_day("2026-08-02", 443601, [0] * 23 + [2])
    vacio = {"fecha": "2026-08-03", "indice": None, "detalle": []}

    elegido = _dia_mas_reciente({d["fecha"]: d for d in (bueno, vacio)})

    assert elegido["fecha"] == "2026-08-02"
    assert elegido["indice"] == 443603


def test_sin_ningun_dia_con_lectura_no_se_elige_nada():
    """Contrato sin telelectura: se cae al valor del contador (lo hace quien llama)."""
    from custom_components.emasesa.coordinator import _dia_mas_reciente

    assert _dia_mas_reciente({}) is None
    assert _dia_mas_reciente({"2026-08-03": {"fecha": "2026-08-03"}}) is None


def test_el_orden_de_las_fechas_no_es_alfabetico_por_casualidad():
    """Fechas ISO de meses y años distintos, para que no cuele un max() ingenuo."""
    from custom_components.emasesa.coordinator import _dia_mas_reciente

    dias = [
        build_day("2025-12-31", 400000, [1]),
        build_day("2026-01-01", 400001, [1]),
        build_day("2026-09-09", 400002, [1]),
        build_day("2026-10-10", 400003, [1]),
    ]
    elegido = _dia_mas_reciente({d["fecha"]: d for d in dias})
    assert elegido["fecha"] == "2026-10-10"


def _dia_en_curso(fecha: str, indice_inicial: int, horas_con_dato: int) -> dict:
    """Un día EN CURSO tal y como lo manda la API.

    Forma real capturada de la API: `indice` del día a null (aún no ha
    cerrado), 24 horas en el detalle, pero sólo las ya publicadas traen
    lectura; el resto vienen también a null.
    """
    dia = build_day(fecha, indice_inicial, [1] * 24)
    dia["indice"] = None
    for i, hora in enumerate(dia["detalle"]):
        if i >= horas_con_dato:
            hora["indice"] = None
            hora["consumo"] = 0
    dia["consumo"] = horas_con_dato
    return dia


def test_el_dia_en_curso_cuenta_aunque_no_tenga_indice_de_cierre():
    """El caso real que descuadraba sensor y estadísticas.

    El 5 de agosto llegaba con `indice` del día a null y sólo 13 horas
    publicadas, así que se descartaba entero y el sensor se quedaba en el
    día 4 mientras el panel de Energía ya tenía el 5 a mediodía.
    """
    from custom_components.emasesa.coordinator import (
        _dia_mas_reciente,
        _indice_del_dia,
    )

    cerrado = build_day("2026-08-04", 443638, [0] * 23 + [127])
    en_curso = _dia_en_curso("2026-08-05", 443765, horas_con_dato=13)

    elegido = _dia_mas_reciente({d["fecha"]: d for d in (cerrado, en_curso)})

    assert elegido["fecha"] == "2026-08-05"
    # La lectura sale de la última hora publicada, no del cierre del día.
    assert _indice_del_dia(elegido) == 443777


def test_un_dia_sin_ninguna_hora_publicada_se_descarta():
    """El día de mañana llega con todo a null: no puede tapar al de hoy."""
    from custom_components.emasesa.coordinator import _dia_mas_reciente

    ayer = build_day("2026-08-04", 443638, [0] * 23 + [127])
    hoy = _dia_en_curso("2026-08-05", 443765, horas_con_dato=13)
    manana = _dia_en_curso("2026-08-06", 443778, horas_con_dato=0)

    elegido = _dia_mas_reciente({d["fecha"]: d for d in (ayer, hoy, manana)})
    assert elegido["fecha"] == "2026-08-05"


@pytest.mark.parametrize(
    ("dia", "esperado"),
    [
        ({"indice": 443765}, 443765),
        ({"indice": None, "detalle": [{"indice": 10}, {"indice": None}]}, 10),
        ({"indice": None, "detalle": [{"indice": None}]}, None),
        ({"indice": None, "detalle": []}, None),
        ({"indice": None, "detalle": "KO"}, None),
        ({"indice": None}, None),
        ({}, None),
        ({"indice": 0}, 0),
    ],
)
def test_indice_del_dia(dia, esperado):
    """Un índice 0 es una lectura válida, no un 'no hay dato'."""
    from custom_components.emasesa.coordinator import _indice_del_dia

    assert _indice_del_dia(dia) == esperado


# --------------------------------------------------------------------------- #
# Sondeo adaptativo
# --------------------------------------------------------------------------- #
@pytest.fixture
def coord_intervalo(coordinator) -> EmasesaCoordinator:
    """Alias legible: el coordinador ya trae los dos ritmos (6 h / 2 h)."""
    return coordinator


def test_dato_nuevo_espacia_el_sondeo(coord_intervalo):
    """Ya tenemos el día: no hace falta volver en dos horas."""
    coord_intervalo.update_interval = timedelta(hours=2)
    coord_intervalo._ajustar_intervalo("2026-08-05")
    assert coord_intervalo.update_interval == timedelta(hours=6)


def test_sin_novedad_se_vuelve_antes(coord_intervalo):
    """Misma fecha dos ciclos seguidos: seguimos esperando la publicación."""
    coord_intervalo._ajustar_intervalo("2026-08-05")
    assert coord_intervalo.update_interval == timedelta(hours=6)

    coord_intervalo._ajustar_intervalo("2026-08-05")
    assert coord_intervalo.update_interval == timedelta(hours=2)


def test_el_ciclo_sin_fecha_cuenta_como_espera(coord_intervalo):
    """Si la API no devuelve fecha, no se puede dar por bueno el día."""
    coord_intervalo._ajustar_intervalo(None)
    assert coord_intervalo.update_interval == timedelta(hours=2)


def test_al_llegar_el_dia_siguiente_se_vuelve_a_espaciar(coord_intervalo):
    """El ciclo completo: espero, llega el dato, me relajo."""
    coord_intervalo._ajustar_intervalo("2026-08-05")
    coord_intervalo._ajustar_intervalo("2026-08-05")
    assert coord_intervalo.update_interval == timedelta(hours=2)

    coord_intervalo._ajustar_intervalo("2026-08-06")
    assert coord_intervalo.update_interval == timedelta(hours=6)


def test_una_fecha_nula_no_borra_la_ultima_conocida(coord_intervalo):
    """Un fallo puntual no debe hacer que el día siguiente parezca repetido."""
    coord_intervalo._ajustar_intervalo("2026-08-05")
    coord_intervalo._ajustar_intervalo(None)
    assert coord_intervalo._ultima_fecha_dato == "2026-08-05"

    # Y el mismo día sigue sin ser novedad.
    coord_intervalo._ajustar_intervalo("2026-08-05")
    assert coord_intervalo.update_interval == timedelta(hours=2)


def test_los_dos_ritmos_de_sondeo():
    """Los intervalos son fijos y los decide la integración, no el usuario.

    Con 6 h y 2 h salen entre 4 y 12 ciclos al día según haga falta esperar,
    frente a los 8 fijos de cuando era configurable a 3 h.
    """
    assert timedelta(hours=6) == SCAN_INTERVAL
    assert timedelta(hours=2) == SCAN_INTERVAL_ESPERA
    assert SCAN_INTERVAL_ESPERA < SCAN_INTERVAL


def test_el_coordinator_arranca_con_el_intervalo_largo(coordinator):
    """Al montar la entrada aún no se sabe si hay dato: se empieza espaciado."""
    assert coordinator._intervalo_largo == SCAN_INTERVAL
    assert coordinator.update_interval == SCAN_INTERVAL


# --------------------------------------------------------------------------- #
# Metadatos de las estadísticas: campos que cambian según la versión de HA
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_la_estadistica_de_consumo_declara_su_clase_de_unidad(
    coordinator, stats_calls, sample_day
):
    """Sin `unit_class`, Home Assistant 2026.11 rechazará las estadísticas.

    Hasta entonces sólo avisa en el registro, y el aviso pide al usuario que
    abra una incidencia en nuestro repositorio.
    """
    from custom_components.emasesa.coordinator import _SOPORTA_UNIT_CLASS

    await coordinator._import_statistics([sample_day], PRECIO_ALTO)

    metadata, _ = stats_calls[0]
    if _SOPORTA_UNIT_CLASS:
        # El consumo va en m³ y es convertible a otras unidades de volumen.
        assert metadata["unit_class"] == "volume"
        assert metadata["unit_of_measurement"] == "m³"
    else:
        assert "unit_class" not in metadata


@pytest.mark.asyncio
async def test_la_estadistica_de_coste_no_declara_clase_de_unidad(
    coordinator, stats_calls, sample_day
):
    """El dinero no se convierte de unidad, así que su clase es nula."""
    from custom_components.emasesa.coordinator import _SOPORTA_UNIT_CLASS

    await coordinator._import_statistics([sample_day], PRECIO_ALTO)

    coste = next(m for m, _ in stats_calls if m["statistic_id"].endswith("_cost"))
    assert coste["unit_of_measurement"] == "EUR"
    if _SOPORTA_UNIT_CLASS:
        assert coste["unit_class"] is None


def test_los_metadatos_se_adaptan_a_la_version_de_home_assistant():
    """Ni `mean_type` ni `unit_class` existen en todas las versiones."""
    from custom_components.emasesa.coordinator import (
        _MEAN_TYPE_NONE,
        _SOPORTA_UNIT_CLASS,
    )

    meta: dict = {}
    EmasesaCoordinator._completar_metadata(meta, "volume")

    if _MEAN_TYPE_NONE is not None:
        assert meta["mean_type"] == _MEAN_TYPE_NONE
        assert "has_mean" not in meta
    else:
        assert meta["has_mean"] is False
    assert ("unit_class" in meta) is _SOPORTA_UNIT_CLASS


@pytest.mark.asyncio
async def test_un_fallo_del_recorder_no_tumba_la_actualizacion(
    coordinator, monkeypatch, sample_day, caplog
):
    """Escribir estadísticas no puede dejar sin datos al resto de sensores.

    Home Assistant ya cambió una vez lo que exige en los metadatos
    (`mean_type`) y volverá a hacerlo en 2026.11 (`unit_class`). Sin esta red,
    esa excepción subiría hasta el coordinator y dejaría en "no disponible"
    el contador, las facturas y los embalses, que no tienen culpa de nada.
    """

    def _revienta(hass, metadata, stats):
        raise KeyError("unit_class")

    monkeypatch.setattr(coordinator_module, "async_add_external_statistics", _revienta)

    # Todo lo que consulta a la API, en blanco: sólo interesa que no propague.
    coordinator.client.get_latest_consumption = AsyncMock(return_value=sample_day)
    coordinator.client.get_meter_info = AsyncMock(return_value={})
    coordinator.client.get_consumption = AsyncMock(return_value=[sample_day])
    coordinator.client.get_consumption_valuation = AsyncMock(return_value={})
    coordinator.client.get_invoices = AsyncMock(return_value=[])
    coordinator.client.get_reservoirs = AsyncMock(return_value={})
    coordinator.client.get_network_actions = AsyncMock(return_value=[])

    datos = await coordinator._async_update_data()

    # La actualización termina y los sensores conservan su valor.
    assert datos["contract_id"] == CONTRACT_ID
    assert datos["total_m3"] == pytest.approx(443.601)
    assert "No se pudieron importar las estadísticas" in caplog.text
