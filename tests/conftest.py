"""Fixtures y datos de ejemplo compartidos por los tests de EMASESA.

Los payloads reproducen la forma REAL de las respuestas de la API privada de
la app "Mi Emasesa" (reverseada), p. ej.::

    {
        "fecha": "2026-07-31",
        "consumo": 16,
        "indice": 443601,
        "estado": "OK",
        "detalle": [
            {"hora": "00", "consumo": 0, "indice": 443585, "nocturno": false},
            ...,
        ],
    }

Todo (consumos e índice del contador) viene en LITROS.

Estos tests no necesitan una instancia de Home Assistant corriendo: importan
los módulos de la integración directamente.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import aiohttp
import pytest
import pytest_asyncio

# El repositorio no es un paquete instalable: metemos su raíz en sys.path para
# poder importar `custom_components.emasesa.*` (funciona como namespace package
# de PEP 420, sin necesidad de custom_components/__init__.py).
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _patch_aioresponses_for_modern_aiohttp() -> None:
    """Compatibilidad aioresponses 0.7.9 <-> aiohttp >= 3.14.

    aiohttp 3.14 añadió `stream_writer` como argumento OBLIGATORIO de
    `ClientResponse.__init__`, y aioresponses todavía no lo pasa: sin esto,
    cualquier respuesta simulada falla con TypeError. El parche se aplica sólo
    si hace falta, así que cuando aioresponses lo arregle queda en nada.
    """
    import inspect

    from aiohttp import ClientResponse
    from aioresponses import core as aioresponses_core

    param = inspect.signature(ClientResponse.__init__).parameters.get("stream_writer")
    if param is None or param.default is not inspect.Parameter.empty:
        return
    if getattr(aioresponses_core.ClientResponse, "_emasesa_compat", False):
        return

    class _CompatClientResponse(ClientResponse):
        _emasesa_compat = True

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs.setdefault("stream_writer", Mock(output_size=0))
            super().__init__(*args, **kwargs)

    aioresponses_core.ClientResponse = _CompatClientResponse


_patch_aioresponses_for_modern_aiohttp()


# El modo asyncio que exige pytest-homeassistant-custom-component se
# configura en `pytest.ini`, en la raíz del repositorio.


# --------------------------------------------------------------------------- #
# Constantes de ejemplo
# --------------------------------------------------------------------------- #
CONTRACT_ID = "4711326"
USERNAME = "28123456Z"
PASSWORD = "s3cr3t0"
DEVICE_ID = "ha-emasesa-0123456789abcdef"

APP_TOKEN = "b0f5c2e4-1a2b-3c4d-5e6f-708192a3b4c5"
USER_TOKEN = "9d8c7b6a-5f4e-3d2c-1b0a-abcdefabcdef"
ONLINE_USER_ID = 862417

# Horas que la API marca como nocturnas (se usa para detectar caudal de fuga).
NIGHT_HOURS = frozenset({"00", "01", "02", "03", "04", "05", "06"})

# Perfil horario realista de un día de verano: 16 L en total.
CONSUMOS_2026_07_31 = [
    0,
    0,
    0,
    0,
    0,
    0,
    1,
    3,
    0,
    0,
    0,
    2,
    0,
    0,
    0,
    0,
    0,
    4,
    0,
    0,
    5,
    1,
    0,
    0,
]
INDICE_INICIAL_2026_07_31 = 443585

CONSUMOS_2026_07_30 = [
    0,
    0,
    0,
    0,
    0,
    0,
    2,
    6,
    1,
    0,
    0,
    3,
    0,
    0,
    0,
    0,
    1,
    7,
    2,
    0,
    4,
    0,
    0,
    0,
]
INDICE_INICIAL_2026_07_30 = 443559


# --------------------------------------------------------------------------- #
# Constructores de payloads
# --------------------------------------------------------------------------- #
def build_day(
    fecha: str,
    indice_inicial: int,
    consumos: list[int],
    estado: str = "OK",
    horas: list[str] | None = None,
) -> dict[str, Any]:
    """Construye un día tal y como lo devuelve /consumos/contrato/{id}.

    El `indice` de cada hora es la lectura del contador AL EMPEZAR esa hora,
    y el `indice` del día es la lectura al terminarlo (inicial + consumo).

    `horas` permite forzar la lista de etiquetas horarias (para el día de 25 h
    del cambio de horario de octubre, donde la hora "02" aparece dos veces).
    """
    if horas is None:
        horas = [f"{h:02d}" for h in range(len(consumos))]
    assert len(horas) == len(consumos)

    detalle: list[dict[str, Any]] = []
    indice = indice_inicial
    for hora, consumo in zip(horas, consumos, strict=False):
        detalle.append(
            {
                "hora": hora,
                "consumo": consumo,
                "indice": indice,
                "nocturno": hora in NIGHT_HOURS and consumo > 0,
            }
        )
        indice += consumo

    return {
        "fecha": fecha,
        "consumo": sum(consumos),
        "indice": indice,
        "estado": estado,
        "detalle": detalle,
    }


def day_2026_07_31() -> dict[str, Any]:
    """Día de ejemplo idéntico al de la respuesta real (indice final 443601)."""
    return build_day("2026-07-31", INDICE_INICIAL_2026_07_31, list(CONSUMOS_2026_07_31))


def day_2026_07_30() -> dict[str, Any]:
    """Día anterior, encadenado con el de 31/07 (443559 -> 443585)."""
    return build_day("2026-07-30", INDICE_INICIAL_2026_07_30, list(CONSUMOS_2026_07_30))


def day_dst_octubre(indice_inicial: int = 500000) -> dict[str, Any]:
    """25/10/2026: domingo del cambio de horario en Europe/Madrid (día de 25 h).

    La hora local "02" aparece DOS veces (02:00 CEST y 02:00 CET).
    """
    horas = (
        [f"{h:02d}" for h in range(3)]  # 00, 01, 02  (CEST)
        + ["02"]  # 02 repetida (CET)
        + [f"{h:02d}" for h in range(3, 24)]  # 03..23
    )
    consumos = [
        0,
        0,
        1,
        2,
        0,
        0,
        3,
        8,
        5,
        0,
        1,
        4,
        2,
        0,
        0,
        0,
        6,
        9,
        3,
        1,
        0,
        2,
        0,
        0,
        0,
    ]
    return build_day("2026-10-25", indice_inicial, consumos, horas=horas)


# --------------------------------------------------------------------------- #
# Respuestas de autenticación (forma real del endpoint autenticarUsuario)
# --------------------------------------------------------------------------- #
APP_TOKEN_RESPONSE: dict[str, Any] = {
    "access_token": APP_TOKEN,
    "scope": "am_application_scope default",
    "token_type": "Bearer",
    "expires_in": 3600,
}

LOGIN_OK_RESPONSE: dict[str, Any] = {
    "codigo": "0",
    "estado": "OK",
    "confianza": "S",
    "mensaje": {
        "estado_aut": "AUTENTICADO",
        "token": {
            "access_token": USER_TOKEN,
            "refresh_token": "2f1e0d9c-8b7a-6543-2109-fedcbafedcba",
            "token_type": "Bearer",
            "expires_in": 3400,
            "scope": "am_application_scope default",
        },
        "usuario": {
            "usuarios_online_id": ONLINE_USER_ID,
            "usuario": USERNAME,
            "nombre": "NOMBRE APELLIDO APELLIDO",
            "email": "usuario@example.com",
            "movil": "6XXXXX123",
        },
    },
}

# Cuenta con doble factor y dispositivo aún no de confianza: el servidor manda
# un SMS y devuelve el canal, SIN token.
LOGIN_2FA_RESPONSE: dict[str, Any] = {
    "codigo": "0",
    "estado": "OK",
    "confianza": "N",
    "mensaje": {
        "estado_aut": "PENDIENTE_VALIDACION_DOBLE_FACTOR",
        "canal_doble_factor_autenticacion": "S",
        "usuario": {
            "usuarios_online_id": ONLINE_USER_ID,
            "usuario": USERNAME,
            "movil": "6XXXXX123",
        },
    },
}

LOGIN_BAD_CREDENTIALS_RESPONSE: dict[str, Any] = {
    "codigo": "401",
    "estado": "KO",
    "mensaje": "Usuario o contraseña incorrectos",
}

CONTRACT_SAMPLE: dict[str, Any] = {
    "contratos_id": int(CONTRACT_ID),
    "numero_contrato": "0012345678",
    "direccion_suministro": "C/ EJEMPLO 1 ES:1 PL:03 PT:B",
    "poblacion": "SEVILLA",
    "relacion": "TI",
    "favorito": "S",
    "vigente": "S",
    "usuarios_online_id": ONLINE_USER_ID,
}


# --------------------------------------------------------------------------- #
# Doble de aiohttp.ClientSession que registra las peticiones tal cual
# --------------------------------------------------------------------------- #
class _FakeResponse:
    """Respuesta mínima compatible con `async with session.get(...) as resp`."""

    def __init__(self, status: int, text: str) -> None:
        self.status = status
        self._text = text

    async def text(self) -> str:
        return self._text

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class FakeSession:
    """Sesión falsa que guarda las URL EXACTAS (objetos yarl) y las cabeceras.

    A diferencia de aioresponses, no normaliza ni reordena la query string, así
    que sirve para comprobar la codificación literal del `$filter` de OData.
    """

    def __init__(self, responses: list[tuple[int, str]] | None = None) -> None:
        self.responses = list(responses or [])
        self.requests: list[dict[str, Any]] = []

    def _next(self) -> _FakeResponse:
        if self.responses:
            status, text = self.responses.pop(0)
        else:
            status, text = 200, "{}"
        return _FakeResponse(status, text)

    def get(self, url: Any, headers: dict[str, str] | None = None, **kw: Any):
        self.requests.append(
            {"method": "GET", "url": url, "headers": headers or {}, **kw}
        )
        return self._next()

    def post(
        self,
        url: Any,
        headers: dict[str, str] | None = None,
        data: Any = None,
        **kw: Any,
    ):
        self.requests.append(
            {
                "method": "POST",
                "url": url,
                "headers": headers or {},
                "data": data,
                **kw,
            }
        )
        return self._next()


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def aiohttp_session():
    """Sesión aiohttp real (las peticiones las intercepta aioresponses)."""
    async with aiohttp.ClientSession() as session:
        yield session


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def sample_day() -> dict[str, Any]:
    return day_2026_07_31()


@pytest.fixture
def sample_days() -> list[dict[str, Any]]:
    return [day_2026_07_30(), day_2026_07_31()]
