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
    coord.hass = object()  # nunca se usa: el recorder está parcheado
    coord.client = AsyncMock()
    coord.contract_id = CONTRACT_ID
    coord.statistic_id = f"{DOMAIN}:{CONTRACT_ID}_water"
    coord.cost_statistic_id = f"{DOMAIN}:{CONTRACT_ID}_water_cost"
    coord._tz = None
    coord._last_stat = AsyncMock(return_value=None)
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
    """Julio en Madrid es UTC+2: la hora local 00 es 22:00 UTC del día anterior."""
    await coordinator._import_statistics([sample_day])
    _, stats = stats_calls[0]

    starts = [s["start"] for s in stats]
    assert starts[0] == datetime(2026, 7, 30, 22, 0, tzinfo=UTC)
    assert starts[-1] == datetime(2026, 7, 31, 21, 0, tzinfo=UTC)
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

    # 02 CEST -> 00:00 UTC ; 02 CET (fold=1) -> 01:00 UTC.
    assert starts[2] == datetime(2026, 10, 25, 0, 0, tzinfo=UTC)
    assert starts[3] == datetime(2026, 10, 25, 1, 0, tzinfo=UTC)
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
