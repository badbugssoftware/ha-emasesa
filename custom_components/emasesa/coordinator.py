"""Coordinador de actualización para EMASESA."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.location import distance

from .api import (
    EmasesaAuthError,
    EmasesaClient,
    EmasesaError,
    EmasesaTwoFactorRequired,
    parse_hour_dt,
)
from .const import (
    DEFAULT_INCIDENT_RADIUS,
    DOMAIN,
    INITIAL_BACKFILL_DAYS,
    LEAK_HOUR_END,
    LEAK_HOUR_START,
    LEAK_MIN_LITERS,
    LEAK_NIGHTS,
    LITERS_PER_M3,
    MAX_BACKFILL_DAYS,
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
        incident_radius_m: int = DEFAULT_INCIDENT_RADIUS,
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
        self.incident_radius_m = incident_radius_m
        self._tz = None

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            today_raw = await self.client.get_latest_consumption(self.contract_id)
            meter_raw = await self.client.get_meter_info(self.contract_id)

            # Ventana de histórico según el hueco real ya almacenado.
            days = await self._days_to_backfill()
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

        contador = (
            meter_raw.get("datosContador", {}) if isinstance(meter_raw, dict) else {}
        )
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
                valoracion = val.get("valoracionConsumo") or {}
                periodo = {
                    "desde": f_ini,
                    "proxima_factura": val.get("fechaProximaFacturacion"),
                    "ultima_telelectura": val.get("fechaUltimaTelelectura"),
                    "consumo_medio_l_dia": val.get("consumoMedio"),
                    "valoracion": valoracion.get("valoracion"),
                    "valoracion_texto": valoracion.get("texto"),
                }
                if consumo_periodo_m3 and f_ini:
                    hoy = dt_util.now().date().strftime("%Y-%m-%d")
                    sim = await self.client.simulate_invoice(
                        self.contract_id, consumo_periodo_m3, f_ini, hoy
                    )
                    if isinstance(sim, dict) and sim.get("importe") is not None:
                        coste_periodo = round(float(sim["importe"]), 2)
                        precio_m3 = round(coste_periodo / float(consumo_periodo_m3), 4)
            _LOGGER.debug(
                "Coste periodo EMASESA: consumo=%s m3 importe=%s EUR "
                "precio=%s EUR/m3 periodo=%s",
                consumo_periodo_m3,
                coste_periodo,
                precio_m3,
                periodo,
            )
        except EmasesaError as err:
            _LOGGER.warning("[EMASESA] no se pudo estimar el coste: %s", err)

        # Importa las estadísticas (consumo + coste) ya con el precio €/m³.
        await self._import_statistics(list(days_data.values()), precio_m3)

        # --- Extras (no críticos: si fallan, la integración sigue) ----------
        factura = await self._safe(self._fetch_last_invoice(), "facturas") or {}
        embalses = await self._safe(self._fetch_reservoirs(), "embalses") or {}
        incidencias = (
            await self._safe(self._fetch_nearby_incidents(), "incidencias de red") or {}
        )
        fuga = self._detect_leak(list(days_data.values()))

        return {
            "contract_id": self.contract_id,
            "coste_periodo_eur": coste_periodo,
            "precio_m3_eur": precio_m3,
            "consumo_periodo_m3": consumo_periodo_m3,
            "periodo_facturacion": periodo,
            "factura": factura,
            "embalses": embalses,
            "incidencias": incidencias,
            "fuga": fuga,
            "averia_estimacion": bool(meter_raw.get("averiaForzarEstimacion"))
            if isinstance(meter_raw, dict)
            else False,
            "incidencia_pendiente": bool(meter_raw.get("incidenciaOTPendienteUsuario"))
            if isinstance(meter_raw, dict)
            else False,
            "fecha": today.get("fecha"),
            "consumo_hoy_l": today.get("consumo"),
            "consumo_diurno_l": today.get("consumo_diurno"),
            "consumo_nocturno_l": today.get("consumo_nocturno"),
            "indice_l": indice_l,
            "total_m3": (indice_l / LITERS_PER_M3)
            if isinstance(indice_l, (int, float))
            else None,
            "meter": {
                "indice_m3": meter_raw.get("indice")
                if isinstance(meter_raw, dict)
                else None,
                "fecha_lectura": meter_raw.get("fecha")
                if isinstance(meter_raw, dict)
                else None,
                "fabricante": contador.get("fabricante"),
                "modelo": contador.get("modelo"),
                "numero_serie": contador.get("numeroSerie"),
                "nbiot": contador.get("nbiot"),
            },
        }

    async def async_reload_history(self, days: int) -> None:
        """Reimporta el histórico de los últimos `days` días (servicio)."""
        date_to = dt_util.now().date()
        date_from = date_to - timedelta(days=days)
        history = await self._fetch_history_chunked(date_from, date_to)
        precio = (self.data or {}).get("precio_m3_eur")
        await self._import_statistics(
            [d for d in history if isinstance(d, dict) and d.get("fecha")], precio
        )
        _LOGGER.info("Histórico de EMASESA reimportado: %s días", days)

    async def _safe(self, coro, what: str) -> Any:
        """Ejecuta una llamada opcional; si falla, avisa y devuelve None.

        Estos datos son complementarios: un fallo suyo no debe tumbar la
        actualización del consumo, que es lo importante.
        """
        try:
            return await coro
        except EmasesaError as err:
            _LOGGER.debug("No se pudo obtener %s: %s", what, err)
            return None

    async def _fetch_last_invoice(self) -> dict[str, Any]:
        """Última factura emitida + deuda pendiente total."""
        facturas = await self.client.get_invoices(self.contract_id, top=6)
        if not facturas:
            return {}
        ultima = facturas[0]
        pendiente = sum(
            float(f.get("importe_pendiente") or 0)
            for f in facturas
            if str(f.get("estado_cobro_codigo", "")).upper() == "P"
        )
        return {
            "numero": ultima.get("numero_factura"),
            "importe": ultima.get("total_con_iva"),
            "fecha_emision": ultima.get("fecha_emision"),
            "estado_cobro": ultima.get("estado_cobro_texto"),
            "consumo_m3": ultima.get("consumo"),
            "dias": ultima.get("consumo_dias"),
            "periodo_desde": ultima.get("fecha_inicio_periodo"),
            "periodo_hasta": ultima.get("fecha_fin_periodo"),
            "pendiente_total": round(pendiente, 2),
        }

    async def _fetch_reservoirs(self) -> dict[str, Any]:
        """Embalses que abastecen a Sevilla (% de llenado conjunto)."""
        data = await self.client.get_reservoirs()
        embalses = (data or {}).get("embalses") or []
        vol = sum(float(e.get("vol_embalsado") or 0) for e in embalses)
        cap = sum(float(e.get("capacidad") or 0) for e in embalses)
        return {
            "fecha": (data or {}).get("fecha"),
            "porc_llenado": round(vol / cap * 100, 1) if cap else None,
            "vol_embalsado_hm3": round(vol, 2),
            "capacidad_hm3": round(cap, 2),
            "detalle": {
                e.get("embalse"): e.get("porc_llenado")
                for e in embalses
                if e.get("embalse")
            },
        }

    async def _fetch_nearby_incidents(self) -> dict[str, Any]:
        """Incidencias de la red de EMASESA cercanas a la vivienda."""
        actuaciones = await self.client.get_network_actions()
        home_lat = self.hass.config.latitude
        home_lon = self.hass.config.longitude
        cercanas: list[dict[str, Any]] = []
        for act in actuaciones:
            lat, lon = act.get("latitud"), act.get("longitud")
            if lat is None or lon is None:
                continue
            dist = distance(home_lat, home_lon, float(lat), float(lon))
            if dist is not None and dist <= self.incident_radius_m:
                cercanas.append(
                    {
                        "categoria": act.get("categoria"),
                        "direccion": act.get("direccion"),
                        "inicio": act.get("inicio"),
                        "tipo": act.get("tipo_actuacion"),
                        "distancia_m": round(dist),
                    }
                )
        cercanas.sort(key=lambda x: x["distancia_m"])
        return {
            "total_ciudad": len(actuaciones),
            "cercanas": cercanas,
            "radio_m": self.incident_radius_m,
        }

    def _detect_leak(self, days: list[dict[str, Any]]) -> dict[str, Any]:
        """Posible fuga: consumo continuo en TODAS las horas de madrugada.

        Un hogar normal tiene al menos una hora sin consumo entre las 02:00 y
        las 05:00. Si durante varios días seguidos no hay ninguna hora a cero,
        suele indicar un goteo permanente (cisterna, grifo, tubería).
        """
        completos = [
            d
            for d in days
            if isinstance(d.get("detalle"), list) and len(d["detalle"]) >= 24
        ]
        completos.sort(key=lambda d: d.get("fecha", ""))
        recientes = completos[-LEAK_NIGHTS:]
        if len(recientes) < LEAK_NIGHTS:
            return {"detectada": False, "noches": 0, "min_l_h": None}

        minimos: list[float] = []
        for day in recientes:
            franja = [
                float(h.get("consumo") or 0)
                for h in day["detalle"]
                if str(h.get("hora", "")).isdigit()
                and LEAK_HOUR_START <= int(h["hora"]) <= LEAK_HOUR_END
            ]
            if not franja:
                return {"detectada": False, "noches": 0, "min_l_h": None}
            minimos.append(min(franja))

        detectada = all(m >= LEAK_MIN_LITERS for m in minimos)
        return {
            "detectada": detectada,
            "noches": len(minimos),
            "min_l_h": min(minimos) if minimos else None,
            "desde": recientes[0].get("fecha") if detectada else None,
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

    async def _last_stat(self, statistic_id: str) -> dict[str, Any] | None:
        """Última fila almacenada de una estadística externa (o None)."""
        res = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics,
            self.hass,
            1,
            statistic_id,
            True,
            {"sum", "start"},
        )
        rows = (res or {}).get(statistic_id)
        return rows[0] if rows else None

    async def _days_to_backfill(self) -> int:
        """Días de histórico a pedir: cubre el hueco real desde lo ya guardado.

        Evita reimportar 60 días en cada reinicio de HA (antes se usaba una
        bandera en memoria) y, a la vez, rellena huecos largos si la API o HA
        han estado caídos más que la ventana normal de actualización.
        """
        last = await self._last_stat(self.statistic_id)
        if not last:
            return INITIAL_BACKFILL_DAYS
        last_dt = dt_util.utc_from_timestamp(last["start"])
        gap = (dt_util.utcnow() - last_dt).days + 1
        return max(UPDATE_BACKFILL_DAYS, min(gap, MAX_BACKFILL_DAYS))

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
                # El 'indice' que EMASESA da para la hora H es la lectura al
                # COMIENZO de esa hora, o sea el cierre de la hora anterior.
                # Home Assistant espera en la fila con start=H la lectura al
                # FINAL de H, así que restamos una hora. Sin esto el histórico
                # queda desplazado 60 min y el consumo de la última hora de la
                # noche se atribuye al día siguiente (verificado contra el
                # export oficial de consumos de EMASESA).
                start = naive.replace(tzinfo=self._tz, fold=fold).astimezone(
                    dt_util.UTC
                ) - timedelta(hours=1)
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

        # --- Coste: acumulado por INCREMENTOS, nunca reescribiendo el pasado ---
        # El precio efectivo €/m³ cambia dentro del ciclo (la cuota fija se
        # reparte entre más m³). Si el coste se derivase del índice absoluto
        # (indice × precio_actual), al reimportar días ya escritos la 'sum'
        # bajaría y el panel de Energía lo tomaría como reset del contador.
        cost_stats: list[StatisticData] = []
        if precio_eur_m3:
            last_cost = await self._last_stat(self.cost_statistic_id)
            if last_cost:
                cost_sum = float(last_cost["sum"] or 0.0)
                last_cost_ts = float(last_cost["start"])
            else:
                cost_sum = 0.0
                last_cost_ts = None

            prev_l: float | None = None
            for start, indice_l in points:
                ts = start.timestamp()
                if last_cost_ts is not None and ts <= last_cost_ts:
                    # Ya contabilizada: sirve solo de referencia para el delta.
                    prev_l = indice_l
                    continue
                if prev_l is not None:
                    delta_m3 = max(0.0, (indice_l - prev_l) / LITERS_PER_M3)
                    cost_sum += delta_m3 * float(precio_eur_m3)
                    cost_stats.append(
                        StatisticData(
                            start=start,
                            state=round(cost_sum, 4),
                            sum=round(cost_sum, 4),
                        )
                    )
                prev_l = indice_l

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
