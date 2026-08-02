"""Coordinador de actualización para EMASESA."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    EmasesaAuthError,
    EmasesaClient,
    EmasesaError,
    parse_hour_dt,
)
from .const import (
    ATTRIBUTION,
    DOMAIN,
    INITIAL_BACKFILL_DAYS,
    LITERS_PER_M3,
    UPDATE_BACKFILL_DAYS,
)

_LOGGER = logging.getLogger(__name__)

_TZ_NAME = "Europe/Madrid"


class EmasesaCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Sondea la API de EMASESA e importa el histórico horario a estadísticas."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: EmasesaClient,
        contract_id: str,
        scan_interval: timedelta,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{contract_id}",
            update_interval=scan_interval,
        )
        self.client = client
        self.contract_id = str(contract_id)
        self.statistic_id = f"{DOMAIN}:{self.contract_id}_water"
        self._did_backfill = False
        self._tz = None

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            today_raw = await self.client.get_latest_consumption(self.contract_id)
            meter_raw = await self.client.get_meter_info(self.contract_id)

            # Ventana de histórico: amplia el primer arranque, corta después.
            days = INITIAL_BACKFILL_DAYS if not self._did_backfill else UPDATE_BACKFILL_DAYS
            date_to = dt_util.now().date()
            date_from = date_to - timedelta(days=days)
            history = await self._fetch_history_chunked(date_from, date_to)
        except EmasesaAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except EmasesaError as err:
            raise UpdateFailed(str(err)) from err

        # Reúne todos los días (histórico + el último, que puede ser parcial de hoy).
        days_data: dict[str, dict[str, Any]] = {}
        for day in history:
            if isinstance(day, dict) and day.get("fecha"):
                days_data[day["fecha"]] = day
        if isinstance(today_raw, dict) and today_raw.get("fecha"):
            days_data[today_raw["fecha"]] = today_raw

        await self._import_statistics(list(days_data.values()))
        self._did_backfill = True

        contador = meter_raw.get("datosContador", {}) if isinstance(meter_raw, dict) else {}
        today = today_raw if isinstance(today_raw, dict) else {}
        indice_l = today.get("indice")

        return {
            "contract_id": self.contract_id,
            "fecha": today.get("fecha"),
            "consumo_hoy_l": today.get("consumo"),
            "consumo_diurno_l": today.get("consumo_diurno"),
            "consumo_nocturno_l": today.get("consumo_nocturno"),
            "indice_l": indice_l,
            "total_m3": (indice_l / LITERS_PER_M3) if isinstance(indice_l, (int, float)) else None,
            "meter": {
                "indice_m3": meter_raw.get("indice") if isinstance(meter_raw, dict) else None,
                "fecha_lectura": meter_raw.get("fecha") if isinstance(meter_raw, dict) else None,
                "fabricante": contador.get("fabricante"),
                "modelo": contador.get("modelo"),
                "numero_serie": contador.get("numeroSerie"),
                "nbiot": contador.get("nbiot"),
            },
        }

    async def _fetch_history_chunked(
        self, date_from: date, date_to: date
    ) -> list[dict[str, Any]]:
        """Pide el histórico en tramos de 30 días (como la app oficial)."""
        chunk = timedelta(days=30)
        out: list[dict[str, Any]] = []
        start = date_from
        while start <= date_to:
            end = min(start + chunk, date_to)
            out.extend(await self.client.get_consumption(self.contract_id, start, end))
            start = end + timedelta(days=1)
        return out

    async def _import_statistics(self, days: list[dict[str, Any]]) -> None:
        """Convierte el detalle horario en estadísticas externas (panel Energía).

        Usamos el 'indice' (lectura acumulada del contador, en litros) como
        suma absoluta en m³. Al ser un contador monotónico, la reimportación
        es idempotente y el panel de Energía calcula el consumo por diferencias.
        """
        if not days:
            return
        if self._tz is None:
            self._tz = await dt_util.async_get_time_zone(_TZ_NAME)

        # Ordena por fecha y aplana a (datetime_utc, indice_litros).
        points: list[tuple[datetime, float]] = []
        for day in sorted(days, key=lambda d: d.get("fecha", "")):
            fecha = day.get("fecha")
            detalle = day.get("detalle") or []
            if not fecha or not isinstance(detalle, list):
                continue
            seen_hours: set[str] = set()
            for item in detalle:
                indice = item.get("indice")
                hora = item.get("hora")
                if indice is None or hora is None:
                    continue
                hora_s = str(hora)
                # En el cambio de hora de octubre (día de 25 h) la hora local 02
                # aparece dos veces; la segunda ocurrencia usa fold=1 para no
                # resolver al mismo instante UTC que la primera.
                fold = 1 if hora_s in seen_hours else 0
                seen_hours.add(hora_s)
                naive = parse_hour_dt(fecha, hora_s)
                start = naive.replace(tzinfo=self._tz, fold=fold).astimezone(dt_util.UTC)
                points.append((start, float(indice)))

        if not points:
            return

        points.sort(key=lambda p: p[0])

        # 'sum' monotónica (clamp) para que el panel de Energía calcule consumos
        # positivos aunque un índice estimado retroceda; 'state' conserva la
        # lectura real de esa hora.
        running_max = float("-inf")
        stats: list[StatisticData] = []
        for start, indice_l in points:
            running_max = max(running_max, indice_l)
            stats.append(
                StatisticData(
                    start=start,
                    state=indice_l / LITERS_PER_M3,
                    sum=running_max / LITERS_PER_M3,
                )
            )

        metadata = StatisticMetaData(
            has_mean=False,
            has_sum=True,
            name=f"EMASESA consumo {self.contract_id}",
            source=DOMAIN,
            statistic_id=self.statistic_id,
            unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        )
        async_add_external_statistics(self.hass, metadata, stats)
        _LOGGER.debug(
            "Importadas %d estadísticas horarias en %s", len(stats), self.statistic_id
        )
