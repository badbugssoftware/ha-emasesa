"""Tests del cliente HTTP (`custom_components/emasesa/api.py`).

Cubren las tres cosas que más se han roto en la práctica:

  * las cabeceras exactas que exige el servidor (User-Agent de okhttp y
    Content-Type de formulario en /oauth2/token),
  * la interpretación de las tres respuestas posibles de autenticarUsuario,
  * la codificación literal del `$filter` de OData (%20 y %27, nunca '+').
"""

from __future__ import annotations

import json
from datetime import date, datetime
from unittest.mock import AsyncMock

import pytest
import yarl
from aioresponses import CallbackResult, aioresponses

from custom_components.emasesa.api import (
    EmasesaAuthError,
    EmasesaClient,
    EmasesaError,
    EmasesaTwoFactorRequired,
    _loads,
    parse_hour_dt,
)
from custom_components.emasesa.const import (
    API_BASE,
    CLIENT_BASIC,
    SISTEMA,
    TOKEN_URL,
)

from .conftest import (
    APP_TOKEN,
    APP_TOKEN_RESPONSE,
    CONTRACT_ID,
    CONTRACT_SAMPLE,
    DEVICE_ID,
    LOGIN_2FA_RESPONSE,
    LOGIN_BAD_CREDENTIALS_RESPONSE,
    LOGIN_OK_RESPONSE,
    ONLINE_USER_ID,
    PASSWORD,
    USER_TOKEN,
    USERNAME,
    FakeSession,
    day_2026_07_31,
)

LOGIN_URL = f"{API_BASE}/login/autenticarUsuario?sistema={SISTEMA}"

OKHTTP_UA = "okhttp/2.1.0"


def _client(session) -> EmasesaClient:
    return EmasesaClient(session, USERNAME, PASSWORD, DEVICE_ID)


def _recorder(store: dict, result: CallbackResult) -> object:
    """Callback de aioresponses que guarda la petición y devuelve `result`."""

    def _cb(url, **kwargs):
        store["url"] = url
        store["headers"] = kwargs.get("headers") or {}
        store["data"] = kwargs.get("data")
        return result

    return _cb


# --------------------------------------------------------------------------- #
# _get_app_token
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_app_token_envia_form_urlencoded_y_user_agent_okhttp(aiohttp_session):
    """Sin Content-Type de formulario el servidor responde 415; sin el UA de
    okhttp, 401. Ambas cabeceras son obligatorias."""
    seen: dict = {}
    with aioresponses() as m:
        m.post(
            TOKEN_URL,
            callback=_recorder(
                seen, CallbackResult(status=200, payload=APP_TOKEN_RESPONSE)
            ),
        )
        token = await _client(aiohttp_session)._get_app_token()

    assert token == APP_TOKEN

    headers = seen["headers"]
    assert headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert "json" not in headers["Content-Type"].lower()
    assert headers["User-Agent"] == OKHTTP_UA
    assert headers["Authorization"] == f"Basic {CLIENT_BASIC}"
    assert headers["Accept"] == "*/*"

    # grant_type viaja en la query, no en el cuerpo (el cuerpo va vacío).
    assert "grant_type=client_credentials" in str(seen["url"])
    assert not seen["data"]


@pytest.mark.asyncio
async def test_app_token_415_lanza_error(aiohttp_session):
    with aioresponses() as m:
        m.post(TOKEN_URL, status=415, body="<html>Unsupported Media Type</html>")
        with pytest.raises(EmasesaError, match="415"):
            await _client(aiohttp_session)._get_app_token()


@pytest.mark.asyncio
async def test_app_token_sin_access_token_lanza_error(aiohttp_session):
    with aioresponses() as m:
        m.post(TOKEN_URL, status=200, payload={"scope": "default"})
        with pytest.raises(EmasesaError, match="access_token"):
            await _client(aiohttp_session)._get_app_token()


# --------------------------------------------------------------------------- #
# login(): los tres casos de la respuesta real
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_login_ok_guarda_token_y_usuario(aiohttp_session):
    seen: dict = {}
    with aioresponses() as m:
        m.post(TOKEN_URL, status=200, payload=APP_TOKEN_RESPONSE)
        m.post(
            LOGIN_URL,
            callback=_recorder(
                seen, CallbackResult(status=200, payload=LOGIN_OK_RESPONSE)
            ),
        )
        client = _client(aiohttp_session)
        await client.login()

    assert client._user_token == USER_TOKEN
    assert client.online_user_id == ONLINE_USER_ID
    # expires_in 3400 menos 120 s de margen.
    assert client._token_expiry > 0

    headers = seen["headers"]
    assert headers["Authorization"] == f"Bearer {APP_TOKEN}"
    assert headers["User-Agent"] == OKHTTP_UA
    assert headers["Content-Type"] == "application/json; charset=UTF-8"

    body = json.loads(seen["data"])
    assert body == {
        "usuario": USERNAME,
        "contrasena": PASSWORD,
        "id_dispositivo": DEVICE_ID,
    }
    assert "pin" not in body


@pytest.mark.asyncio
async def test_login_con_pin_incluye_el_campo_pin(aiohttp_session):
    seen: dict = {}
    with aioresponses() as m:
        m.post(TOKEN_URL, status=200, payload=APP_TOKEN_RESPONSE)
        m.post(
            LOGIN_URL,
            callback=_recorder(
                seen, CallbackResult(status=200, payload=LOGIN_OK_RESPONSE)
            ),
        )
        client = _client(aiohttp_session)
        await client.login(pin="483920")

    assert json.loads(seen["data"])["pin"] == "483920"
    assert client._user_token == USER_TOKEN


@pytest.mark.asyncio
async def test_login_reto_doble_factor(aiohttp_session):
    """200 sin token pero con canal_doble_factor_autenticacion -> reto 2FA."""
    with aioresponses() as m:
        m.post(TOKEN_URL, status=200, payload=APP_TOKEN_RESPONSE)
        m.post(LOGIN_URL, status=200, payload=LOGIN_2FA_RESPONSE)
        client = _client(aiohttp_session)
        with pytest.raises(EmasesaTwoFactorRequired) as excinfo:
            await client.login()

    err = excinfo.value
    assert err.channel == "S"  # SMS
    assert err.detail == "PENDIENTE_VALIDACION_DOBLE_FACTOR"
    # Un reto 2FA NO es un fallo de credenciales.
    assert isinstance(err, EmasesaError)
    assert not isinstance(err, EmasesaAuthError)
    assert client._user_token is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_login_credenciales_invalidas(aiohttp_session, status):
    with aioresponses() as m:
        m.post(TOKEN_URL, status=200, payload=APP_TOKEN_RESPONSE)
        m.post(LOGIN_URL, status=status, payload=LOGIN_BAD_CREDENTIALS_RESPONSE)
        client = _client(aiohttp_session)
        with pytest.raises(EmasesaAuthError):
            await client.login()

    assert client._user_token is None


@pytest.mark.asyncio
async def test_login_200_con_mensaje_de_texto_es_auth_error(aiohttp_session):
    """El servidor a veces devuelve 200 con `mensaje` como string de error."""
    with aioresponses() as m:
        m.post(TOKEN_URL, status=200, payload=APP_TOKEN_RESPONSE)
        m.post(LOGIN_URL, status=200, payload=LOGIN_BAD_CREDENTIALS_RESPONSE)
        with pytest.raises(EmasesaAuthError, match="Usuario o contraseña"):
            await _client(aiohttp_session).login()


@pytest.mark.asyncio
async def test_login_sin_token_ni_reto_es_auth_error(aiohttp_session):
    with aioresponses() as m:
        m.post(TOKEN_URL, status=200, payload=APP_TOKEN_RESPONSE)
        m.post(LOGIN_URL, status=200, payload={"codigo": "0", "mensaje": {}})
        with pytest.raises(EmasesaAuthError, match="sin token"):
            await _client(aiohttp_session).login()


@pytest.mark.asyncio
async def test_login_500_no_es_auth_error(aiohttp_session):
    """Un 5xx debe ser error genérico (reintentable), no de credenciales."""
    with aioresponses() as m:
        m.post(TOKEN_URL, status=200, payload=APP_TOKEN_RESPONSE)
        m.post(LOGIN_URL, status=500, body="Internal Server Error")
        with pytest.raises(EmasesaError) as excinfo:
            await _client(aiohttp_session).login()
    assert not isinstance(excinfo.value, EmasesaAuthError)


# --------------------------------------------------------------------------- #
# get_contracts: codificación del $filter de OData
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_get_contracts_codifica_filter_con_percent20_y_percent27(fake_session):
    client = _client(fake_session)
    client.online_user_id = ONLINE_USER_ID
    client._get = AsyncMock(return_value={"value": [CONTRACT_SAMPLE]})

    contratos = await client.get_contracts()
    assert contratos == [CONTRACT_SAMPLE]

    path = client._get.await_args.args[0]
    filtro = path.split("$filter=")[1].split("&")[0]

    assert filtro == (
        f"usuarios_online_id%20eq%20{ONLINE_USER_ID}%20and%20relacion%20ne%20%27AF%27"
    )
    # Los espacios NUNCA como '+': algunos parsers OData lo rechazan.
    assert "+" not in filtro
    assert " " not in filtro
    assert "'" not in filtro

    # El $orderby conserva las comas sin codificar y los espacios como %20.
    orderby = path.split("$orderby=")[1].split("&")[0]
    assert orderby == ("favorito%20desc,vigente%20desc,poblacion,direccion_suministro")
    assert "+" not in orderby

    assert f"sistema={SISTEMA}" in path
    assert "$expand=direcciones_contacto" in path
    assert "$top=20" in path


@pytest.mark.asyncio
async def test_get_url_no_recodifica_el_filter(fake_session):
    """`_get` construye la URL con yarl `encoded=True`: %20/%27 sobreviven."""
    client = _client(fake_session)
    client._user_token = USER_TOKEN
    client._token_expiry = float("inf")

    path = (
        f"/contratos?sistema={SISTEMA}"
        f"&$filter=usuarios_online_id%20eq%20{ONLINE_USER_ID}"
        "%20and%20relacion%20ne%20%27AF%27"
    )
    fake_session.responses.append((200, json.dumps({"value": []})))
    await client._get(path)

    url = fake_session.requests[0]["url"]
    assert isinstance(url, yarl.URL)
    raw = str(url)
    assert "%20eq%20" in raw
    assert "%27AF%27" in raw
    assert "+" not in raw
    assert fake_session.requests[0]["headers"]["User-Agent"] == OKHTTP_UA
    assert fake_session.requests[0]["headers"]["Authorization"] == (
        f"Bearer {USER_TOKEN}"
    )


@pytest.mark.asyncio
async def test_get_contracts_fuerza_login_si_no_hay_online_user_id(fake_session):
    client = _client(fake_session)
    client._get = AsyncMock(return_value={"value": []})

    async def _fake_login(pin=None):
        client.online_user_id = ONLINE_USER_ID

    client.login = AsyncMock(side_effect=_fake_login)

    await client.get_contracts()
    client.login.assert_awaited_once()
    assert str(ONLINE_USER_ID) in client._get.await_args.args[0]


@pytest.mark.asyncio
async def test_get_contracts_sin_online_user_id_tras_login_falla(fake_session):
    client = _client(fake_session)
    client.login = AsyncMock()  # no rellena online_user_id
    client._get = AsyncMock()

    with pytest.raises(EmasesaError, match="usuarios_online_id"):
        await client.get_contracts()
    client._get.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_contracts_acepta_lista_plana(fake_session):
    client = _client(fake_session)
    client.online_user_id = ONLINE_USER_ID
    client._get = AsyncMock(return_value=[CONTRACT_SAMPLE])
    assert await client.get_contracts() == [CONTRACT_SAMPLE]


# --------------------------------------------------------------------------- #
# Resto de endpoints de datos
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_get_consumption_formatea_fechas(fake_session):
    client = _client(fake_session)
    client._get = AsyncMock(return_value=[day_2026_07_31()])

    dias = await client.get_consumption(
        CONTRACT_ID, date(2026, 7, 1), date(2026, 7, 31)
    )
    path = client._get.await_args.args[0]

    assert f"/consumos/contrato/{CONTRACT_ID}?sistema={SISTEMA}" in path
    assert "fechaDesde=2026-07-01" in path
    assert "fechaHasta=2026-07-31" in path
    assert "horaria=true" in path
    assert dias[0]["indice"] == 443601


@pytest.mark.asyncio
async def test_get_consumption_envuelve_dict_en_lista(fake_session):
    client = _client(fake_session)
    dia = day_2026_07_31()
    client._get = AsyncMock(return_value=dia)
    assert await client.get_consumption(
        CONTRACT_ID, date(2026, 7, 31), date(2026, 7, 31)
    ) == [dia]


@pytest.mark.asyncio
async def test_simulate_invoice_redondea_consumo_a_entero(fake_session):
    client = _client(fake_session)
    client._get = AsyncMock(return_value={"importe": 41.37})

    await client.simulate_invoice(CONTRACT_ID, 12.6, "2026-06-15", date(2026, 8, 2))
    path = client._get.await_args.args[0]

    assert "consumo=13" in path  # round(12.6) -> 13
    assert "fechaDesde=2026-06-15" in path
    assert "fechaHasta=2026-08-02" in path
    assert f"idContrato={CONTRACT_ID}" in path


@pytest.mark.asyncio
async def test_get_reintenta_una_vez_ante_401(fake_session):
    """Un 401 en un GET invalida el token y reintenta exactamente una vez."""
    client = _client(fake_session)
    client._user_token = USER_TOKEN
    client._token_expiry = float("inf")
    client.login = AsyncMock(
        side_effect=lambda pin=None: setattr(client, "_user_token", "nuevo")
    )
    fake_session.responses.extend([(401, ""), (200, json.dumps({"indice": 443.601}))])

    data = await client._get(f"/lecturas/informacion/{CONTRACT_ID}")

    assert data == {"indice": 443.601}
    assert len(fake_session.requests) == 2
    assert fake_session.requests[1]["headers"]["Authorization"] == "Bearer nuevo"


@pytest.mark.asyncio
async def test_get_401_persistente_lanza_error(fake_session):
    client = _client(fake_session)
    client._user_token = USER_TOKEN
    client._token_expiry = float("inf")
    client.login = AsyncMock(
        side_effect=lambda pin=None: setattr(client, "_user_token", "nuevo")
    )
    fake_session.responses.extend([(401, ""), (401, "no autorizado")])

    with pytest.raises(EmasesaError, match="401"):
        await client._get("/consumos/contrato/1/ultimo")


@pytest.mark.asyncio
async def test_register_trusted_device_manda_confianza_s(fake_session):
    client = _client(fake_session)
    client._user_token = USER_TOKEN
    client._token_expiry = float("inf")
    fake_session.responses.append((200, "{}"))

    await client.register_trusted_device()

    req = fake_session.requests[0]
    assert req["method"] == "POST"
    assert "/dispositivos?" in str(req["url"])
    assert req["headers"]["User-Agent"] == OKHTTP_UA
    body = json.loads(req["data"])
    assert body["confianza"] == "S"
    assert body["id_dispositivo"] == DEVICE_ID


# --------------------------------------------------------------------------- #
# parse_hour_dt y _loads
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("dia", "hora", "esperado"),
    [
        ("2026-07-31", "00", datetime(2026, 7, 31, 0, 0)),
        ("2026-07-31", "09", datetime(2026, 7, 31, 9, 0)),
        ("2026-07-31", "23", datetime(2026, 7, 31, 23, 0)),
        ("2026-10-25", "02", datetime(2026, 10, 25, 2, 0)),
        ("2026-01-01", "0", datetime(2026, 1, 1, 0, 0)),  # sin cero delante
        ("2026-12-31", "7", datetime(2026, 12, 31, 7, 0)),
    ],
)
def test_parse_hour_dt(dia, hora, esperado):
    assert parse_hour_dt(dia, hora) == esperado


def test_parse_hour_dt_devuelve_naive():
    """Debe ser naive: el coordinator le pone después la zona Europe/Madrid."""
    assert parse_hour_dt("2026-07-31", "12").tzinfo is None


def test_parse_hour_dt_recorre_el_detalle_real():
    dia = day_2026_07_31()
    momentos = [parse_hour_dt(dia["fecha"], h["hora"]) for h in dia["detalle"]]
    assert len(momentos) == 24
    assert momentos == sorted(momentos)
    assert momentos[0] == datetime(2026, 7, 31, 0, 0)
    assert momentos[-1] == datetime(2026, 7, 31, 23, 0)


@pytest.mark.parametrize("texto", ["", "   ", "\n"])
def test_loads_cuerpo_vacio(texto):
    assert _loads(texto) == {}


def test_loads_json_valido():
    assert _loads('{"fecha": "2026-07-31", "indice": 443601}') == {
        "fecha": "2026-07-31",
        "indice": 443601,
    }


@pytest.mark.asyncio
async def test_register_trusted_device_no_reautentica(monkeypatch):
    """Regresión: registrar el dispositivo NO debe disparar un login nuevo.

    Se llama justo tras validar el doble factor y, como el dispositivo todavía
    no es de confianza, un login aquí volvería a exigir código y rompería el
    alta (traceback real: _after_login -> register_trusted_device ->
    _ensure_token -> login -> EmasesaTwoFactorRequired).
    """
    session = FakeSession([])
    client = EmasesaClient(session, USERNAME, PASSWORD, DEVICE_ID)

    llamadas = []

    async def _login_espia(*args, **kwargs):
        llamadas.append(kwargs)
        raise AssertionError("register_trusted_device no debe reautenticar")

    monkeypatch.setattr(client, "login", _login_espia)

    # Sin sesión previa: error claro, pero NUNCA un login.
    with pytest.raises(EmasesaError):
        await client.register_trusted_device()
    assert llamadas == []
