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
    EmasesaTwoFactorRequired,
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

# HA 2025.11+ sustituye has_mean por mean_type en StatisticMetaData; usamos
# mean_type si está disponible y caemos a has_mean en versiones antiguas.
try:
    from homeassistant.components.recorder.models import StatisticMeanType

    _MEAN_TYPE_NONE = StatisticMeanType.NONE
except ImportError:  # pragma: no cover
    _MEAN_TYPE_NONE = None


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
        self.cost_statistic_id = f"{DOMAIN}:{self.contract_id}_water_cost"
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
        except EmasesaTwoFactorRequired as err:
            # El dispositivo dejó de ser de confianza: pedir reauth en vez de
            # reintentar en bucle (cada reintento dispararía un SMS nuevo).
            raise ConfigEntryAuthFailed(
                "El dispositivo requiere doble factor de nuevo; reconfigura EMASESA"
            ) from err
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

        contador = meter_raw.get("datosContador", {}) if isinstance(meter_raw, dict) else {}
        today = today_raw if isinstance(today_raw, dict) else {}
        indice_l = today.get("indice")

        # --- Coste estimado del periodo (simulador oficial de EMASESA) -------
        coste_periodo = None
        precio_m3 = None
        consumo_periodo_m3 = None
        periodo: dict[str, Any] = {}
        try:
            val = await self.client.get_consumption_valuation(self.contract_id)
            if isinstance(val, dict):
                consumo_periodo_m3 = val.get("consumo")
                f_ini = val.get("fechaFinUltimaFactura")
                periodo = {
                    "desde": f_ini,
                    "proxima_factura": val.get("fechaProximaFacturacion"),
                }
                if consumo_periodo_m3 and f_ini:
                    hoy = dt_util.now().date().strftime("%Y-%m-%d")
                    sim = await self.client.simulate_invoice(
                        self.contract_id, consumo_periodo_m3, f_ini, hoy
                    )
                    if isinstance(sim, dict) and sim.get("importe") is not None:
                        coste_periodo = round(float(sim["importe"]), 2)
                        precio_m3 = round(
                            coste_periodo / float(consumo_periodo_m3), 4
                        )
            _LOGGER.debug(
                "Coste periodo EMASESA: consumo=%s m3 importe=%s EUR "
                "precio=%s EUR/m3 periodo=%s",
                consumo_periodo_m3, coste_periodo, precio_m3, periodo,
            )
        except EmasesaError as err:
            _LOGGER.warning("[EMASESA] no se pudo estimar el coste: %s", err)

        # Importa las estadísticas (consumo + coste) ya con el precio €/m³.
        await self._import_statistics(list(days_data.values()), precio_m3)
        self._did_backfill = True

        return {
            "contract_id": self.contract_id,
            "coste_periodo_eur": coste_periodo,
            "precio_m3_eur": precio_m3,
            "consumo_periodo_m3": consumo_periodo_m3,
            "periodo_facturacion": periodo,
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

    @staticmethod
    def _apply_mean_type(meta: StatisticMetaData) -> None:
        """has_mean/mean_type según versión de HA (evita el deprecation warning)."""
        if _MEAN_TYPE_NONE is not None:
            meta["mean_type"] = _MEAN_TYPE_NONE
        else:
            meta["has_mean"] = False

    async def _import_statistics(
        self, days: list[dict[str, Any]], precio_eur_m3: float | None = None
    ) -> None:
        """Convierte el detalle horario en estadísticas externas (panel Energía).

        Usamos el 'indice' (lectura acumulada del contador, en litros) como
        suma absoluta en m³. Al ser un contador monotónico, la reimportación
        es idempotente y el panel de Energía calcula el consumo por diferencias.

        Si se conoce el precio €/m³, importa además una estadística de COSTE
        acumulado (€) para usarla como "entidad de costes totales" en Energía.
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
        cost_stats: list[StatisticData] = []
        for start, indice_l in points:
            running_max = max(running_max, indice_l)
            total_m3 = running_max / LITERS_PER_M3
            stats.append(
                StatisticData(
                    start=start,
                    state=indice_l / LITERS_PER_M3,
                    sum=total_m3,
                )
            )
            if precio_eur_m3:
                eur = round(total_m3 * float(precio_eur_m3), 4)
                cost_stats.append(StatisticData(start=start, state=eur, sum=eur))

        metadata = StatisticMetaData(
            has_sum=True,
            name=f"EMASESA consumo {self.contract_id}",
            source=DOMAIN,
            statistic_id=self.statistic_id,
            unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        )
        self._apply_mean_type(metadata)
        async_add_external_statistics(self.hass, metadata, stats)

        if cost_stats:
            cost_meta = StatisticMetaData(
                has_sum=True,
                name=f"EMASESA coste {self.contract_id}",
                source=DOMAIN,
                statistic_id=self.cost_statistic_id,
                unit_of_measurement="EUR",
            )
            self._apply_mean_type(cost_meta)
            async_add_external_statistics(self.hass, cost_meta, cost_stats)

        _LOGGER.debug(
            "Importadas %d estadísticas de consumo en %s%s",
            len(stats),
            self.statistic_id,
            f" y {len(cost_stats)} de coste en {self.cost_statistic_id}"
            if cost_stats
            else "",
        )
