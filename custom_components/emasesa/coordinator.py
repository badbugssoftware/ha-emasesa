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
    SCAN_INTERVAL,
    SCAN_INTERVAL_ESPERA,
    UPDATE_BACKFILL_DAYS,
)

_LOGGER = logging.getLogger(__name__)

_TZ_NAME = "Europe/Madrid"

# Caché de los datos que no dependen del contrato (embalses, red).
_CACHE_KEY = "_global_cache"
GLOBAL_CACHE_TTL = timedelta(hours=1)

# HA 2025.11+ sustituye has_mean por mean_type en StatisticMetaData; usamos
# mean_type si está disponible y caemos a has_mean en versiones antiguas.
try:
    from homeassistant.components.recorder.models import StatisticMeanType

    _MEAN_TYPE_NONE = StatisticMeanType.NONE
except ImportError:  # pragma: no cover
    _MEAN_TYPE_NONE = None

# HA 2026.x añade `unit_class` a StatisticMetaData: dice con qué conversor se
# puede cambiar de unidad la serie. Sin él, Home Assistant avisa en el registro
# y a partir de 2026.11 dejará de aceptar las estadísticas.
#
# Se detecta en vez de darlo por hecho, para no romper en versiones anteriores
# donde ese campo no existe.
_SOPORTA_UNIT_CLASS = "unit_class" in getattr(StatisticMetaData, "__annotations__", {})
try:
    from homeassistant.util.unit_conversion import VolumeConverter

    _UNIT_CLASS_VOLUMEN: str | None = VolumeConverter.UNIT_CLASS
except (ImportError, AttributeError):  # pragma: no cover
    _UNIT_CLASS_VOLUMEN = None


def _indice_del_dia(dia: dict[str, Any]) -> float | None:
    """Lectura del contador de un día: la de cierre o la de su última hora.

    Un día ya cerrado trae su `indice` (lectura al terminarlo). El día EN
    CURSO lo trae a `null` —todavía no ha acabado— pero su detalle horario ya
    tiene lecturas hasta la última hora publicada, y las horas posteriores
    vienen también a `null`. Por eso se recorre el detalle de atrás hacia
    delante buscando la última hora con lectura de verdad.
    """
    indice = dia.get("indice")
    if isinstance(indice, (int, float)):
        return indice
    detalle = dia.get("detalle")
    if not isinstance(detalle, list):
        return None
    for hora in reversed(detalle):
        if isinstance(hora, dict) and isinstance(hora.get("indice"), (int, float)):
            return hora["indice"]
    return None


def _dia_mas_reciente(days_data: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """El día con lectura del contador más fresco de todos los que hay.

    No basta con el endpoint `/ultimo`: devuelve el último día CERRADO,
    mientras que el histórico horario ya trae el día en curso a medias. Fiarse
    sólo de `/ultimo` dejaba el sensor del índice mostrando un valor más
    antiguo que el que la propia integración acababa de escribir en las
    estadísticas del panel de Energía.

    Se descartan los días sin ninguna lectura (el de mañana, o un hueco de
    telelectura): un día más reciente pero vacío no debe tapar a uno anterior
    que sí tiene dato.
    """
    con_lectura = [
        d
        for d in days_data.values()
        if d.get("fecha") and _indice_del_dia(d) is not None
    ]
    if not con_lectura:
        return None
    return max(con_lectura, key=lambda d: str(d["fecha"]))


class EmasesaCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Sondea la API de EMASESA e importa el histórico horario a estadísticas."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: EmasesaClient,
        contract_id: str,
        incident_radius_m: int = DEFAULT_INCIDENT_RADIUS,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{contract_id}",
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        self.contract_id = str(contract_id)
        self.statistic_id = f"{DOMAIN}:{self.contract_id}_water"
        self.cost_statistic_id = f"{DOMAIN}:{self.contract_id}_water_cost"
        self.incident_radius_m = incident_radius_m
        # None = usar la ubicación de Home Assistant.
        self.latitude = latitude
        self.longitude = longitude
        self._tz = None
        self._warned_no_hourly = False
        # Si un import no produce puntos, la próxima ventana se acorta en vez
        # de reintentar el histórico completo (ver _days_to_backfill).
        self._last_import_empty = False

        # Sondeo adaptativo, ver _ajustar_intervalo.
        self._intervalo_largo = SCAN_INTERVAL
        self._intervalo_corto = SCAN_INTERVAL_ESPERA
        self._ultima_fecha_dato: str | None = None

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

        # 'or {}' y no un default de .get(): la API puede mandar la clave a null
        # (contratos sin contador asignado) y el default solo actúa si falta.
        contador = (
            (meter_raw.get("datosContador") or {})
            if isinstance(meter_raw, dict)
            else {}
        )
        today = _dia_mas_reciente(days_data) or (
            today_raw if isinstance(today_raw, dict) else {}
        )
        indice_l = _indice_del_dia(today)

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
        #
        # Blindado a propósito: escribir en el recorder no puede tumbar la
        # actualización entera. Si Home Assistant cambia lo que exige de los
        # metadatos —ya pasó con `mean_type` y vuelve a pasar con
        # `unit_class`—, sin esto la excepción subiría hasta el coordinator y
        # dejaría en "no disponible" TODAS las entidades: el contador, las
        # facturas, los embalses. Y esos datos siguen siendo válidos aunque el
        # histórico del panel de Energía no se pueda escribir.
        try:
            await self._import_statistics(list(days_data.values()), precio_m3)
        except Exception:
            _LOGGER.exception(
                "No se pudieron importar las estadísticas del contrato %s; "
                "los sensores siguen actualizándose con normalidad",
                self.contract_id,
            )

        # --- Extras (no críticos: si fallan, la integración sigue) ----------
        factura = await self._safe(self._fetch_last_invoice(), "facturas") or {}
        embalses = await self._safe(self._fetch_reservoirs(), "embalses") or {}
        incidencias = (
            await self._safe(self._fetch_nearby_incidents(), "incidencias de red") or {}
        )
        fuga = self._detect_leak(list(days_data.values()))
        self._ajustar_intervalo(today.get("fecha"))

        return {
            "contract_id": self.contract_id,
            # Lo publica el sensor del índice: es el id que hay que elegir en
            # el panel de Energía para usar el histórico horario.
            "statistic_id": self.statistic_id,
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
            # Sin telelectura no hay índice horario, pero /lecturas/informacion
            # ya nos ha dado la lectura del contador en m³: mejor eso que dejar
            # el sensor principal en "desconocido".
            "total_m3": (indice_l / LITERS_PER_M3)
            if isinstance(indice_l, (int, float))
            else (
                float(meter_raw["indice"])
                if isinstance(meter_raw, dict)
                and isinstance(meter_raw.get("indice"), (int, float))
                else None
            ),
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

    def _ajustar_intervalo(self, fecha_dato: str | None) -> None:
        """Espacia el sondeo cuando ya se tiene el dato del día.

        EMASESA publica la telelectura una vez al día y a una hora que varía:
        medido en una instalación real, un día el dato llevaba 26 h de retraso
        y otro 12. Con intervalo fijo, o se machaca la API o se llega tarde.

        Así que mientras no llegue nada nuevo se vuelve antes, y en cuanto
        aparece el día siguiente se espacia. Además cada instalación acaba
        desfasada según cuándo le llegue su dato, en vez de que todas llamen
        a la vez.
        """
        hay_novedad = fecha_dato is not None and fecha_dato != self._ultima_fecha_dato
        if fecha_dato is not None:
            self._ultima_fecha_dato = fecha_dato

        nuevo = self._intervalo_largo if hay_novedad else self._intervalo_corto
        if nuevo == self.update_interval:
            return
        _LOGGER.debug(
            "Sondeo de %s: %s -> %s (%s)",
            self.contract_id,
            self.update_interval,
            nuevo,
            "hay dato nuevo" if hay_novedad else "esperando publicación",
        )
        self.update_interval = nuevo

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

    async def _cached_global(self, clave: str, coro_factory) -> Any:
        """Datos iguales para todos los contratos, pedidos una sola vez.

        Los embalses y las actuaciones de red no dependen del contrato, pero
        antes se pedían una vez por entrada: con varios contratos se
        multiplicaban las llamadas a la API sin ganar nada.
        """
        cache = self.hass.data.setdefault(DOMAIN, {}).setdefault(_CACHE_KEY, {})
        entrada = cache.get(clave)
        ahora = dt_util.utcnow()
        if entrada and (ahora - entrada[0]) < GLOBAL_CACHE_TTL:
            return entrada[1]
        valor = await coro_factory()
        cache[clave] = (ahora, valor)
        return valor

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
        except Exception:
            _LOGGER.warning(
                "Respuesta inesperada al obtener %s; se omite este dato",
                what,
                exc_info=True,
            )
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
        data = await self._cached_global("embalses", self.client.get_reservoirs)
        embalses = (data or {}).get("embalses") or []
        vol = sum(float(e.get("vol_embalsado") or 0) for e in embalses)
        cap = sum(float(e.get("capacidad") or 0) for e in embalses)
        return {
            "fecha": (data or {}).get("fecha"),
            "porc_llenado": round(vol / cap * 100, 1) if cap else None,
            "vol_embalsado_hm3": round(vol, 2),
            "capacidad_hm3": round(cap, 2),
            # Uno por embalse: alimenta su sensor y el desglose que publica
            # el sensor conjunto en sus atributos.
            "por_embalse": [
                {
                    "nombre": e.get("embalse"),
                    "porc_llenado": e.get("porc_llenado"),
                    "vol_embalsado_hm3": e.get("vol_embalsado"),
                    "capacidad_hm3": e.get("capacidad"),
                }
                for e in embalses
                if e.get("embalse")
            ],
        }

    async def _fetch_nearby_incidents(self) -> dict[str, Any]:
        """Incidencias de la red de EMASESA cercanas al suministro.

        Se usa la ubicación configurada para este contrato; si no hay, la de
        Home Assistant. Con varios contratos, las incidencias del segundo no
        son las de la casa.
        """
        actuaciones = await self._cached_global(
            "red_actuaciones", self.client.get_network_actions
        )
        home_lat = (
            self.latitude if self.latitude is not None else self.hass.config.latitude
        )
        home_lon = (
            self.longitude if self.longitude is not None else self.hass.config.longitude
        )
        if home_lat is None or home_lon is None:
            # HA sin ubicación configurada: no se puede medir distancia.
            return {
                "total_ciudad": len(actuaciones),
                "cercanas": [],
                "radio_m": self.incident_radius_m,
                "sin_ubicacion": True,
            }
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
        # Sin noches completas no se puede afirmar que NO haya fuga: el sensor
        # se queda en "desconocido" en vez de decir que todo va bien.
        sin_analizar = {
            "detectada": None,
            "analizado": False,
            "noches": 0,
            "min_l_h": None,
        }
        if len(recientes) < LEAK_NIGHTS:
            return sin_analizar

        minimos: list[float] = []
        for day in recientes:
            franja = [
                float(h.get("consumo") or 0)
                for h in day["detalle"]
                if str(h.get("hora", "")).isdigit()
                and LEAK_HOUR_START <= int(h["hora"]) <= LEAK_HOUR_END
            ]
            if not franja:
                return sin_analizar
            minimos.append(min(franja))

        detectada = all(m >= LEAK_MIN_LITERS for m in minimos)
        return {
            "detectada": detectada,
            "analizado": True,
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

        Si no hay estadísticas Y el intento anterior no produjo ni un punto,
        se deja de insistir con los 60 días: un contador sin telelectura nunca
        va a escribirlas, y sin esto se re-descargaría el histórico completo en
        cada ciclo contra una API privada, para siempre.
        """
        last = await self._last_stat(self.statistic_id)
        if not last:
            if self._last_import_empty:
                return UPDATE_BACKFILL_DAYS
            return INITIAL_BACKFILL_DAYS
        last_dt = dt_util.utc_from_timestamp(last["start"])
        gap = (dt_util.utcnow() - last_dt).days + 1
        return max(UPDATE_BACKFILL_DAYS, min(gap, MAX_BACKFILL_DAYS))

    @staticmethod
    def _completar_metadata(
        meta: StatisticMetaData, unit_class: str | None = None
    ) -> None:
        """Rellena los campos que cambian de una versión de HA a otra.

        Ninguno de los dos se puede poner a secas: `mean_type` no existe antes
        de 2025.11 y `unit_class` no existe antes de 2026, así que se detecta
        qué acepta la versión instalada. Sin `unit_class`, Home Assistant avisa
        en el registro y a partir de 2026.11 rechaza las estadísticas.
        """
        if _MEAN_TYPE_NONE is not None:
            meta["mean_type"] = _MEAN_TYPE_NONE
        else:
            meta["has_mean"] = False
        if _SOPORTA_UNIT_CLASS:
            meta["unit_class"] = unit_class

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
            if not detalle:
                # Sin desglose horario pero con lectura diaria: se emite un
                # único punto al cierre del día (23:00 local menos la hora de
                # desplazamiento, igual que los horarios) para que el panel de
                # Energía tenga al menos una serie diaria.
                indice_dia = day.get("indice")
                if isinstance(indice_dia, (int, float)):
                    naive = parse_hour_dt(fecha, "23")
                    start = naive.replace(tzinfo=self._tz).astimezone(
                        dt_util.UTC
                    ) - timedelta(hours=1)
                    points.append((start, float(indice_dia)))
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
            # Nada que importar: la próxima ventana se acorta para no volver a
            # pedir el histórico completo en cada ciclo.
            self._last_import_empty = True
            # Sin telelectura horaria no hay nada que importar. Se avisa una
            # sola vez: si no, el usuario no entiende por qué el panel de
            # Energía está vacío y no hay ni una línea en el registro.
            if not self._warned_no_hourly:
                self._warned_no_hourly = True
                _LOGGER.warning(
                    "EMASESA no devuelve consumo por horas para el contrato %s: "
                    "el contador no parece tener telelectura NB-IoT. Los sensores "
                    "de factura, embalses y consumo del periodo siguen funcionando, "
                    "pero no habrá histórico horario en el panel de Energía",
                    self.contract_id,
                )
            return

        self._last_import_empty = False
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
        self._completar_metadata(metadata, _UNIT_CLASS_VOLUMEN)
        async_add_external_statistics(self.hass, metadata, stats)

        if cost_stats:
            cost_meta = StatisticMetaData(
                has_sum=True,
                name=f"EMASESA coste {self.contract_id}",
                source=DOMAIN,
                statistic_id=self.cost_statistic_id,
                unit_of_measurement="EUR",
            )
            # El dinero no se convierte de unidad: sin clase.
            self._completar_metadata(cost_meta, None)
            async_add_external_statistics(self.hass, cost_meta, cost_stats)

        _LOGGER.debug(
            "Importadas %d estadísticas de consumo en %s%s",
            len(stats),
            self.statistic_id,
            f" y {len(cost_stats)} de coste en {self.cost_statistic_id}"
            if cost_stats
            else "",
        )
