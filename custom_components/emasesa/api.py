"""Cliente asíncrono para la API privada de Mi Emasesa.

Flujo de autenticación (reverseado de la app oficial):

  1. Token de aplicación (client_credentials):
       POST /oauth2/token?grant_type=client_credentials
       Authorization: Basic <CLIENT_BASIC>
       -> { access_token, token_type: "Bearer", expires_in }

  2. Login de usuario:
       POST /miemasesa/api/v1.0/login/autenticarUsuario?sistema=3
       Authorization: Bearer <token_app>
       body: { usuario, contrasena, id_dispositivo [, pin] }
       -> { codigo, estado, confianza,
            mensaje: { estado_aut, token: { access_token, refresh_token, ... },
                       usuario: { usuarios_online_id, ... } } }

     Si la cuenta tiene doble factor y el dispositivo no es de confianza,
     el servidor envía un código (SMS/email) y hay que reintentar el login
     añadiendo el campo "pin" con ese código.

  3. Llamadas de datos:
       Authorization: Bearer <token_usuario>
"""

from __future__ import annotations

import html
import json
import logging
import re
import time
from datetime import date, datetime
from typing import Any
from urllib.parse import quote

import aiohttp
import yarl

from .const import (
    API_BASE,
    CLIENT_BASIC,
    SINAC_DETALLE_URL,
    SINAC_TIMEOUT,
    SISTEMA,
    TOKEN_URL,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)

# Margen de seguridad antes de que caduque el token de usuario.
_TOKEN_LEEWAY = 120


class EmasesaError(Exception):
    """Error genérico de la API."""


class EmasesaAuthError(EmasesaError):
    """Credenciales inválidas."""


class EmasesaTwoFactorRequired(EmasesaError):
    """El login requiere un código de doble factor.

    Attributes:
        channel: canal por el que se ha enviado ('S' SMS, 'C' correo, ...).
        detail: texto informativo devuelto por el servidor, si lo hay.
    """

    def __init__(self, channel: str | None = None, detail: str | None = None) -> None:
        super().__init__("Se requiere código de doble factor")
        self.channel = channel
        self.detail = detail


def _net_error(err: Exception) -> EmasesaError:
    """Traduce un fallo de red a un error nuestro con un mensaje entendible."""
    if isinstance(err, TimeoutError):
        return EmasesaError("EMASESA no respondió a tiempo")
    return EmasesaError(f"No se pudo contactar con EMASESA: {err}")


class EmasesaClient:
    """Encapsula toda la conversación con la API de Mi Emasesa."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        device_id: str,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._device_id = device_id

        self._user_token: str | None = None
        self._token_expiry: float = 0.0
        self.online_user_id: int | None = None

    # ------------------------------------------------------------------ #
    # Autenticación
    # ------------------------------------------------------------------ #
    async def _get_app_token(self) -> str:
        """Obtiene el token de aplicación (client_credentials)."""
        headers = {
            "Authorization": f"Basic {CLIENT_BASIC}",
            # El servidor exige este Content-Type en /oauth2/token: sin él
            # responde 415 (Unsupported Media Type). El grant_type va en la query.
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "*/*",
            "User-Agent": USER_AGENT,
        }
        try:
            async with self._session.post(TOKEN_URL, headers=headers, data=b"") as resp:
                text = await resp.text()
                status = resp.status
        except (aiohttp.ClientError, TimeoutError) as err:
            raise _net_error(err) from err

        if status != 200:
            raise EmasesaError(
                f"Fallo obteniendo token de app ({status}): {text[:200]}"
            )
        data = _loads(text)
        token = data.get("access_token")
        if not token:
            raise EmasesaError("Respuesta de token de app sin access_token")
        return token

    async def login(self, pin: str | None = None) -> None:
        """Autentica al usuario y guarda el token de sesión.

        Lanza EmasesaTwoFactorRequired si hace falta código de doble factor,
        o EmasesaAuthError si las credenciales son incorrectas.
        """
        app_token = await self._get_app_token()

        body: dict[str, Any] = {
            "usuario": self._username,
            "contrasena": self._password,
            "id_dispositivo": self._device_id,
        }
        if pin:
            body["pin"] = pin

        url = f"{API_BASE}/login/autenticarUsuario?sistema={SISTEMA}"
        # Cabeceras idénticas a las de la app oficial (okhttp). Algún filtro del
        # servidor rechaza peticiones con User-Agent/Content-Type distintos.
        headers = {
            "Authorization": f"Bearer {app_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "*/*",
            "User-Agent": USER_AGENT,
        }
        try:
            async with self._session.post(
                url, headers=headers, data=json.dumps(body)
            ) as resp:
                text = await resp.text()
                status = resp.status
        except (aiohttp.ClientError, TimeoutError) as err:
            raise _net_error(err) from err

        if status in (401, 403):
            raise EmasesaAuthError("Usuario o contraseña incorrectos")
        if status != 200:
            raise EmasesaError(f"Login falló ({status}): {text[:200]}")
        data = _loads(text)

        mensaje = data.get("mensaje")
        _LOGGER.debug(
            "Login EMASESA: estado=%r codigo=%r con_token=%s",
            data.get("estado"),
            data.get("codigo"),
            bool(
                isinstance(mensaje, dict)
                and isinstance(mensaje.get("token"), dict)
                and mensaje["token"].get("access_token")
            ),
        )

        # Éxito = hay token de usuario. No dependemos del valor de 'estado'.
        if not isinstance(mensaje, dict):
            detalle = (
                mensaje
                if isinstance(mensaje, str)
                else (data.get("message") or data.get("codigo") or "desconocido")
            )
            raise EmasesaAuthError(f"Login rechazado: {detalle}")

        token_obj = (
            mensaje.get("token") if isinstance(mensaje.get("token"), dict) else {}
        )
        access_token = token_obj.get("access_token")

        if not access_token:
            # Sin token: doble factor pendiente, o credenciales inválidas.
            canal = mensaje.get("canal_doble_factor_autenticacion")
            estado_aut = mensaje.get("estado_aut")
            if canal or estado_aut:
                raise EmasesaTwoFactorRequired(
                    channel=canal, detail=str(estado_aut) if estado_aut else None
                )
            raise EmasesaAuthError("Login sin token de usuario")

        self._user_token = access_token
        expires_in = int(token_obj.get("expires_in", 3400))
        self._token_expiry = time.time() + expires_in - _TOKEN_LEEWAY

        usuario = mensaje.get("usuario") or {}
        if usuario.get("usuarios_online_id") is not None:
            self.online_user_id = int(usuario["usuarios_online_id"])

        _LOGGER.debug(
            "Login EMASESA correcto (online_user_id=%s, expira en %ss)",
            self.online_user_id,
            expires_in,
        )

    async def _ensure_token(self) -> None:
        """Renueva la sesión si el token ha caducado.

        El dispositivo ya es de confianza tras el primer login, así que
        reautenticar con usuario/contraseña no dispara el doble factor.
        """
        if self._user_token and time.time() < self._token_expiry:
            return
        await self.login()

    async def register_trusted_device(
        self, alias: str = "Home Assistant", modelo: str = "Home Assistant"
    ) -> bool:
        """Marca el device_id como de confianza (confianza='S').

        Igual que la app oficial tras el primer acceso: una vez registrado,
        los logins posteriores con el mismo device_id NO exigen doble factor.
        Sin esto, el coordinator volvería a pedir 2FA en cada arranque.

        Usa el token que ya hay en memoria y NUNCA reautentica: se llama justo
        después de validar el doble factor y, como el dispositivo todavía no es
        de confianza, un login nuevo aquí volvería a exigir un código y
        rompería el alta.
        """
        if not self._user_token:
            raise EmasesaError(
                "No hay sesión activa para registrar el dispositivo de confianza"
            )
        url = (
            f"{API_BASE}/dispositivos?sistema={SISTEMA}"
            f"&usuario={quote(self._username, safe='')}"
        )
        headers = {
            "Authorization": f"Bearer {self._user_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "*/*",
            "User-Agent": USER_AGENT,
        }
        body = {
            "alias": alias,
            "version_app": "3.17.2",
            "id_dispositivo": self._device_id,
            "confianza": "S",
            "modelo": modelo,
            "token_notificaciones": "",
        }
        try:
            async with self._session.post(
                url, headers=headers, data=json.dumps(body)
            ) as resp:
                text = await resp.text()
                status = resp.status
        except (aiohttp.ClientError, TimeoutError) as err:
            raise _net_error(err) from err

        if status not in (200, 201, 204):
            # No es fatal (el alta continúa), pero sí conviene verlo: sin esto
            # el doble factor reaparecerá en cada arranque.
            _LOGGER.error(
                "EMASESA no aceptó registrar el dispositivo de confianza (%s): %s. "
                "Es probable que vuelva a pedirte el código en el próximo arranque",
                status,
                text[:200],
            )
            return False
        _LOGGER.debug("Dispositivo EMASESA registrado como de confianza")
        return True

    async def _get(self, path: str, retry: bool = True) -> Any:
        """GET autenticado que devuelve JSON."""
        await self._ensure_token()
        # encoded=True: yarl NO re-codifica. El path ya lleva %20/%27 donde toca
        # (ver get_contracts); así evitamos que los espacios acaben como '+',
        # que algunos parsers OData rechazan.
        url = yarl.URL(f"{API_BASE}{path}", encoded=True)
        headers = {
            "Authorization": f"Bearer {self._user_token}",
            "Accept": "*/*",
            "User-Agent": USER_AGENT,
        }
        try:
            async with self._session.get(url, headers=headers) as resp:
                text = await resp.text()
                status = resp.status
        except (aiohttp.ClientError, TimeoutError) as err:
            raise _net_error(err) from err

        if status == 401 and retry:
            # Token invalidado antes de tiempo: reautenticar una vez.
            self._user_token = None
            return await self._get(path, retry=False)
        if status != 200:
            raise EmasesaError(f"GET {path} -> {status}: {text[:200]}")
        return _loads(text)

    async def _post(self, path: str, body: dict[str, Any], retry: bool = True) -> Any:
        """POST autenticado que devuelve JSON."""
        await self._ensure_token()
        url = yarl.URL(f"{API_BASE}{path}", encoded=True)
        headers = {
            "Authorization": f"Bearer {self._user_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "*/*",
            "User-Agent": USER_AGENT,
        }
        try:
            async with self._session.post(
                url, headers=headers, data=json.dumps(body)
            ) as resp:
                text = await resp.text()
                status = resp.status
        except (aiohttp.ClientError, TimeoutError) as err:
            raise _net_error(err) from err

        if status == 401 and retry:
            self._user_token = None
            return await self._post(path, body, retry=False)
        if status != 200:
            raise EmasesaError(f"POST {path} -> {status}: {text[:200]}")
        return _loads(text)

    # ------------------------------------------------------------------ #
    # Datos
    # ------------------------------------------------------------------ #
    async def get_contracts(self) -> list[dict[str, Any]]:
        """Lista los contratos (puntos de suministro) del usuario."""
        if self.online_user_id is None:
            # _ensure_token no reautentica si el token sigue vigente; forzamos
            # login para garantizar que tenemos usuarios_online_id.
            await self.login()
        if self.online_user_id is None:
            raise EmasesaError(
                "Login sin 'usuarios_online_id'; no se pueden listar contratos"
            )
        # Sin excluir relacion='AF': si se filtran aquí, un administrador de
        # fincas recibe la lista vacía y el alta le dice "no se han encontrado
        # contratos", cuando en realidad los tiene. La distinción se hace luego
        # en Python, que además permite mostrárselos etiquetados.
        flt = quote(
            f"usuarios_online_id eq {self.online_user_id}",
            safe="",
        )
        orderby = quote(
            "favorito desc,vigente desc,poblacion,direccion_suministro", safe=","
        )
        path = (
            f"/contratos?sistema={SISTEMA}"
            f"&$filter={flt}"
            f"&$orderby={orderby}"
            "&$expand=direcciones_contacto&$top=20"
        )
        data = await self._get(path)
        return data.get("value", []) if isinstance(data, dict) else (data or [])

    async def get_latest_consumption(self, contract_id: str | int) -> dict[str, Any]:
        """Último día disponible con detalle horario."""
        path = f"/consumos/contrato/{contract_id}/ultimo?sistema={SISTEMA}"
        return await self._get(path)

    async def get_consumption(
        self,
        contract_id: str | int,
        date_from: date,
        date_to: date,
    ) -> list[dict[str, Any]]:
        """Consumo horario en un rango de fechas (una entrada por día).

        Las fechas van en formato yyyy-MM-dd (como hace la app).
        """
        df = date_from.strftime("%Y-%m-%d")
        dt = date_to.strftime("%Y-%m-%d")
        path = (
            f"/consumos/contrato/{contract_id}?sistema={SISTEMA}"
            f"&fechaDesde={df}&fechaHasta={dt}"
            "&horaria=true&ultimoConsumoDisponible=false"
        )
        data = await self._get(path)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("value", [data])
        return []

    async def get_meter_info(self, contract_id: str | int) -> dict[str, Any]:
        """Información del contador (índice, fabricante, NB-IoT...)."""
        path = f"/lecturas/informacion/{contract_id}?sistema={SISTEMA}"
        return await self._get(path)

    async def get_consumption_valuation(self, contract_id: str | int) -> dict[str, Any]:
        """Valoración del consumo del periodo en curso.

        Devuelve, entre otros: 'consumo' (m³ del ciclo en curso),
        'fechaFinUltimaFactura' y 'fechaProximaFacturacion'.
        """
        path = f"/consumos/valoracion_consumo/{contract_id}?sistema={SISTEMA}"
        return await self._get(path)

    async def get_invoices(
        self, contract_id: str | int, top: int = 3
    ) -> list[dict[str, Any]]:
        """Últimas facturas del contrato (importe, estado de cobro, periodo)."""
        flt = quote(f"contratos_id eq {contract_id}", safe="")
        # Sin $orderby el servidor no garantiza el orden: "última factura"
        # dependería de la suerte.
        orderby = quote("fecha_emision desc", safe="")
        path = (
            f"/facturas?sistema={SISTEMA}&$filter={flt}"
            f"&$orderby={orderby}&$top={int(top)}"
        )
        data = await self._get(path)
        return data.get("value", []) if isinstance(data, dict) else (data or [])

    async def get_reservoirs(self) -> dict[str, Any]:
        """Estado de los embalses que abastecen a Sevilla."""
        return await self._get(f"/info/embalses?sistema={SISTEMA}")

    async def get_network_actions(self) -> list[dict[str, Any]]:
        """Incidencias y actuaciones en la red (con coordenadas GPS).

        Es un POST con cuerpo vacío, como hace la app.
        """
        data = await self._post(f"/red/actuaciones?sistema={SISTEMA}", {})
        if isinstance(data, dict):
            return data.get("actuaciones") or []
        return []

    async def simulate_invoice(
        self,
        contract_id: str | int,
        consumo_m3: float,
        date_from: date | str,
        date_to: date | str,
    ) -> dict[str, Any]:
        """Simula la factura para un consumo (m³) y periodo -> importe € exacto.

        Es el simulador oficial de la app: EMASESA aplica la tarifa real del
        contrato (cuota fija + tramos + saneamiento + depuración + canon + IVA),
        así que no hay que mantener tablas de tarifas.
        """
        df = (
            date_from.strftime("%Y-%m-%d")
            if isinstance(date_from, date)
            else str(date_from)
        )
        dt = date_to.strftime("%Y-%m-%d") if isinstance(date_to, date) else str(date_to)
        path = (
            f"/facturas/simulacion?sistema={SISTEMA}"
            f"&consumo={round(float(consumo_m3))}"
            f"&fechaDesde={df}&fechaHasta={dt}"
            f"&idContrato={contract_id}"
        )
        return await self._get(path)


def _loads(text: str) -> Any:
    """json.loads tolerante a cuerpos vacíos."""
    if not text or not text.strip():
        return {}
    return json.loads(text)


def parse_hour_dt(day: str, hour: str) -> datetime:
    """Combina 'fecha' (yyyy-MM-dd) y 'hora' ('00'..'23') en un datetime naive."""
    return datetime.strptime(f"{day} {int(hour):02d}", "%Y-%m-%d %H")


# ---------------------------------------------------------------------- #
# Calidad del agua: SINAC (Ministerio de Sanidad)
# ---------------------------------------------------------------------- #
# No es la API de EMASESA sino una web pública, sin autenticación: la ficha de
# una red de abastecimiento se pide con un POST de formulario, igual que hace
# el propio buscador de SINAC al pinchar en la red.

_FILA = re.compile(r"(?is)<tr[^>]*>(.*?)</tr>")
_CELDA = re.compile(r"(?is)<td[^>]*>(.*?)</td>")
_ETIQUETA = re.compile(r"(?s)<[^>]+>")
# Código del parámetro en el anexo del RD 3/2023: "045", "054,1"...
_CODIGO = re.compile(r"^\d{3}(?:,\d+)?$")
_FECHA = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")


async def get_sinac_detail(
    session: aiohttp.ClientSession, cod_municipio: str, id_red: str
) -> str:
    """HTML de la ficha de una red de abastecimiento en SINAC.

    Bastan el id de la red y el municipio: no hace falta sesión ni pasar antes
    por el buscador. La respuesta tarda del orden de 30 s.
    """
    try:
        async with session.post(
            SINAC_DETALLE_URL,
            data={"idRed": id_red, "codMunicipio": cod_municipio},
            timeout=aiohttp.ClientTimeout(total=SINAC_TIMEOUT),
        ) as resp:
            # La cabecera dice UTF-8 y el <meta> ISO-8859-1; manda la cabecera,
            # que es la que acierta. `replace` por si algún día no la acierta.
            text = await resp.text(errors="replace")
            status = resp.status
    except TimeoutError as err:
        raise EmasesaError("SINAC no respondió a tiempo") from err
    except aiohttp.ClientError as err:
        raise EmasesaError(f"No se pudo contactar con SINAC: {err}") from err
    if status != 200:
        raise EmasesaError(f"SINAC respondió {status}")
    return text


def _texto(celda: str) -> str:
    return " ".join(html.unescape(_ETIQUETA.sub(" ", celda)).split())


def _fecha_iso(texto: str) -> str | None:
    """'08/09/2026' -> '2026-09-08'."""
    m = _FECHA.match(texto)
    return f"{m[3]}-{m[2]}-{m[1]}" if m else None


def _numero(texto: str) -> float | None:
    """Valor cuantificado de SINAC ('0.8', '263', '-0.1'; a veces con coma)."""
    try:
        return float(texto.replace(",", "."))
    except ValueError:
        return None


def parse_sinac_detail(page: str) -> dict[str, Any]:
    """Extrae de la ficha de SINAC los parámetros y el último análisis.

    Devuelve::

        {
            "parametros": {
                "045": {"nombre": "Cloro libre residual", "valor": 0.8,
                        "unidad": "mg/L", "fecha": "2026-09-11"},
                ...
            },
            "ultimo_control": {"fecha": "2026-09-11",
                               "calificacion": "AGUA APTA PARA EL CONSUMO"},
        }

    Los parámetros salen de la tabla "Últimos valor notificado de los
    parámetros de la legislación vigente": una fila por parámetro con su
    código legal, que es lo que se usa como clave porque el nombre cambia de
    una tabla a otra ("PH", "Dureza Total (CaCO3)"...). Los que SINAC marca
    como "Sin datos en SINAC" se omiten.

    Si la página no trae ninguna de las dos cosas, se considera que SINAC ha
    cambiado de formato (o ha devuelto una página de error con código 200) y
    se lanza EmasesaError en vez de dejar todos los sensores vacíos sin avisar.
    """
    parametros: dict[str, dict[str, Any]] = {}
    for fila in _FILA.findall(page):
        celdas = [_texto(c) for c in _CELDA.findall(fila)]
        if len(celdas) < 5 or not _CODIGO.match(celdas[0]):
            continue
        valor = _numero(celdas[2])
        if valor is None:
            continue
        parametros.setdefault(
            celdas[0],
            {
                "nombre": celdas[1],
                "valor": valor,
                "unidad": celdas[3],
                "fecha": _fecha_iso(celdas[4]),
            },
        )

    # Los análisis de control van en su propia tabla, del más reciente al más
    # antiguo, justo después de su título y antes de la de análisis completos.
    ultimo_control: dict[str, Any] | None = None
    inicio = page.find("Análisis de control")
    if inicio != -1:
        fin = page.find("Análisis de completo", inicio)
        seccion = page[inicio : fin if fin != -1 else None]
        for fila in _FILA.findall(seccion):
            celdas = [_texto(c) for c in _CELDA.findall(fila)]
            if len(celdas) >= 2 and (fecha := _fecha_iso(celdas[0])):
                ultimo_control = {"fecha": fecha, "calificacion": celdas[1]}
                break

    if not parametros and ultimo_control is None:
        raise EmasesaError("La ficha de SINAC no trae datos de calidad reconocibles")
    return {"parametros": parametros, "ultimo_control": ultimo_control}
