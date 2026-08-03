"""Tests del flujo de configuración de EMASESA.

Aquí es donde han estado los fallos que más han dolido: el login que devolvía
"contraseña incorrecta" cuando en realidad fallaba el User-Agent, la
reautenticación que decía "correcto" y dejaba la integración caída, y el
registro del dispositivo de confianza que rompía el alta si fallaba.

A diferencia del resto de la batería, estos tests SÍ levantan una instancia de
Home Assistant (fixture `hass`), porque lo que se prueba es la máquina de
estados del config flow, no la lógica de la API.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.emasesa.api import (
    EmasesaAuthError,
    EmasesaError,
    EmasesaTwoFactorRequired,
)
from custom_components.emasesa.config_flow import normalize_username
from custom_components.emasesa.const import (
    CONF_CONTRACT_ID,
    CONF_CONTRACT_NUMBER,
    CONF_DEVICE_ID,
    CONF_INCIDENT_RADIUS,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_PASSWORD,
    CONF_SCAN_MINUTES,
    CONF_SUPPLY_ADDRESS,
    CONF_USERNAME,
    DOMAIN,
)
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import CONTRACT_ID, PASSWORD, USERNAME

# --------------------------------------------------------------------------- #
# Infraestructura
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
async def _home_assistant(recorder_mock: Any, enable_custom_integrations: Any) -> None:
    """Prepara Home Assistant para cargar la integración.

    El orden de los argumentos importa: `recorder_mock` tiene que resolverse
    ANTES de que exista la instancia de `hass` (el plugin lo comprueba), y la
    integración declara `recorder` como dependencia porque escribe
    estadísticas de largo plazo.
    """
    return


@pytest.fixture(autouse=True)
def _mock_setup_entry():
    """Evita que crear la entrada arranque de verdad el coordinator.

    Lo que se prueba aquí es el flujo, no el arranque: sin esto cada
    `async_create_entry` intentaría hablar con la API real.
    """
    with patch(
        "custom_components.emasesa.async_setup_entry", return_value=True
    ) as mock:
        yield mock


CONTRATO_A: dict[str, Any] = {
    "contratos_id": int(CONTRACT_ID),
    "numero_contrato": "0012345678",
    "direccion_suministro": "C/ EJEMPLO 1 ES:1 PL:03 PT:B",
    "relacion": "TI",
}
CONTRATO_B: dict[str, Any] = {
    "contratos_id": 9988776,
    "numero_contrato": "0087654321",
    "direccion_suministro": "C/ SEGUNDA 22",
    "relacion": "AF",
}


class Secuencia(list):
    """Marca una lista como "efectos consecutivos", no como valor de retorno.

    Hace falta porque `get_contracts` devuelve precisamente una lista: sin este
    marcador no se podría distinguir "devuelve estos dos contratos" de "falla
    la primera vez y a la segunda devuelve esto".
    """


_SIN_ESPECIFICAR = object()


def make_client(
    *,
    login: Any = None,
    contracts: Any = _SIN_ESPECIFICAR,
    register: Any = None,
) -> MagicMock:
    """Cliente falso con las tres llamadas que usa el flujo.

    Cada argumento acepta una excepción (se lanza), una `Secuencia` (un efecto
    por llamada) o cualquier otro valor (se devuelve tal cual).
    """

    def _async(spec: Any) -> AsyncMock:
        if isinstance(spec, Secuencia):
            return AsyncMock(side_effect=list(spec))
        if isinstance(spec, BaseException) or (
            isinstance(spec, type) and issubclass(spec, BaseException)
        ):
            return AsyncMock(side_effect=spec)
        return AsyncMock(return_value=spec)

    if contracts is _SIN_ESPECIFICAR:
        contracts = [CONTRATO_A]

    client = MagicMock()
    client.login = _async(login)
    client.get_contracts = _async(contracts)
    client.register_trusted_device = _async(register)
    return client


def patch_client(client: MagicMock):
    """Sustituye EmasesaClient en el config flow por el doble indicado."""
    return patch(
        "custom_components.emasesa.config_flow.EmasesaClient", return_value=client
    )


async def start_user_flow(hass: HomeAssistant) -> dict[str, Any]:
    """Abre el flujo de alta y devuelve el primer formulario."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


CREDENCIALES = {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}


# --------------------------------------------------------------------------- #
# normalize_username
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("12345678z", "12345678Z"),
        ("12.345.678-Z", "12345678Z"),
        ("  12345678 z ", "12345678Z"),
        ("12_345_678/z", "12345678Z"),
        ("X1234567L", "X1234567L"),
        ("", ""),
    ],
)
def test_normalize_username(entrada: str, esperado: str) -> None:
    """El servidor sólo acepta el documento compacto y en mayúsculas."""
    assert normalize_username(entrada) == esperado


# --------------------------------------------------------------------------- #
# Alta: camino feliz
# --------------------------------------------------------------------------- #
async def test_alta_con_un_solo_contrato(hass: HomeAssistant) -> None:
    """Con un único suministro no se pregunta nada más: entrada directa."""
    client = make_client()
    result = await start_user_flow(hass)

    with patch_client(client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENCIALES
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "EMASESA 0012345678"
    assert result["data"][CONF_USERNAME] == USERNAME
    assert result["data"][CONF_PASSWORD] == PASSWORD
    assert result["data"][CONF_CONTRACT_ID] == CONTRACT_ID
    assert result["data"][CONF_CONTRACT_NUMBER] == "0012345678"
    assert result["data"][CONF_SUPPLY_ADDRESS] == CONTRATO_A["direccion_suministro"]
    # El id de dispositivo se genera en el alta y viaja en cada petición.
    assert result["data"][CONF_DEVICE_ID]

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == CONTRACT_ID


async def test_alta_normaliza_el_documento(hass: HomeAssistant) -> None:
    """Se guarda 12345678Z aunque el usuario escriba 12.345.678-z."""
    client = make_client()
    result = await start_user_flow(hass)

    with patch_client(client) as ctor:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "12.345.678-z", CONF_PASSWORD: PASSWORD},
        )
        await hass.async_block_till_done()

    assert result["data"][CONF_USERNAME] == "12345678Z"
    # Y al cliente se le pasa ya normalizado, no lo que tecleó el usuario.
    assert ctor.call_args.args[1] == "12345678Z"


async def test_alta_registra_el_dispositivo_de_confianza(hass: HomeAssistant) -> None:
    """Sin este registro, el coordinator pediría doble factor en cada ciclo."""
    client = make_client()
    result = await start_user_flow(hass)

    with patch_client(client):
        await hass.config_entries.flow.async_configure(result["flow_id"], CREDENCIALES)
        await hass.async_block_till_done()

    client.register_trusted_device.assert_awaited_once()


async def test_alta_sigue_aunque_falle_el_registro_de_confianza(
    hass: HomeAssistant,
) -> None:
    """Que el registro falle es molesto, pero no puede impedir el alta."""
    client = make_client(register=EmasesaError("500"))
    result = await start_user_flow(hass)

    with patch_client(client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENCIALES
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY


# --------------------------------------------------------------------------- #
# Alta: errores
# --------------------------------------------------------------------------- #
async def test_alta_credenciales_invalidas_y_reintento(hass: HomeAssistant) -> None:
    """Tras un 401 se vuelve al formulario, y el reintento debe funcionar."""
    result = await start_user_flow(hass)

    with patch_client(make_client(login=EmasesaAuthError("credenciales"))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENCIALES
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_auth"}

    with patch_client(make_client()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENCIALES
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_alta_error_de_red(hass: HomeAssistant) -> None:
    """Un fallo de conexión no se puede confundir con contraseña incorrecta."""
    result = await start_user_flow(hass)

    with patch_client(make_client(login=EmasesaError("timeout"))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENCIALES
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_alta_sin_contratos(hass: HomeAssistant) -> None:
    """Cuenta válida pero sin suministros asociados."""
    result = await start_user_flow(hass)

    with patch_client(make_client(contracts=[])):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENCIALES
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_contracts"


async def test_alta_falla_al_listar_contratos(hass: HomeAssistant) -> None:
    """Autentica pero la API se cae al pedir los contratos."""
    result = await start_user_flow(hass)

    with patch_client(make_client(contracts=EmasesaError("502"))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENCIALES
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_alta_contrato_ya_configurado(hass: HomeAssistant) -> None:
    """El mismo contrato no se puede dar de alta dos veces."""
    MockConfigEntry(
        domain=DOMAIN, unique_id=CONTRACT_ID, data={CONF_USERNAME: USERNAME}
    ).add_to_hass(hass)

    result = await start_user_flow(hass)
    with patch_client(make_client()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENCIALES
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# --------------------------------------------------------------------------- #
# Doble factor
# --------------------------------------------------------------------------- #
async def test_2fa_camino_completo(hass: HomeAssistant) -> None:
    """Cuenta con doble factor: SMS, PIN y entrada creada."""
    client = make_client(login=Secuencia([EmasesaTwoFactorRequired(channel="S"), None]))
    result = await start_user_flow(hass)

    with patch_client(client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENCIALES
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "2fa"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"pin": " 123456 "}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    # El PIN se manda sin espacios: copiar/pegar del SMS los arrastra.
    assert client.login.await_args.kwargs["pin"] == "123456"


async def test_2fa_pin_incorrecto_y_reintento(hass: HomeAssistant) -> None:
    """Un PIN erróneo devuelve al mismo paso, no aborta el alta."""
    client = make_client(
        login=Secuencia(
            [
                EmasesaTwoFactorRequired(channel="S"),
                EmasesaTwoFactorRequired(channel="S"),
                None,
            ]
        )
    )
    result = await start_user_flow(hass)

    with patch_client(client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENCIALES
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"pin": "000000"}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "2fa"
        assert result["errors"] == {"base": "invalid_pin"}

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"pin": "123456"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_2fa_al_pedir_los_contratos(hass: HomeAssistant) -> None:
    """La sesión caduca justo después del login: pedir PIN, no abortar.

    Antes esto salía como "cannot_connect" y el usuario no entendía nada.
    """
    client = make_client(
        contracts=Secuencia([EmasesaTwoFactorRequired(channel="S"), [CONTRATO_A]])
    )
    result = await start_user_flow(hass)

    with patch_client(client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENCIALES
        )
        assert result["step_id"] == "2fa"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"pin": "123456"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_2fa_error_de_red_al_validar(hass: HomeAssistant) -> None:
    """Si la red falla validando el PIN no se puede decir "PIN incorrecto"."""
    client = make_client(
        login=Secuencia([EmasesaTwoFactorRequired(), EmasesaError("timeout")])
    )
    result = await start_user_flow(hass)

    with patch_client(client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENCIALES
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"pin": "123456"}
        )

    assert result["errors"] == {"base": "cannot_connect"}


# --------------------------------------------------------------------------- #
# Varios contratos
# --------------------------------------------------------------------------- #
async def test_seleccion_de_contrato(hass: HomeAssistant) -> None:
    """Con varios suministros hay que elegir cuál se da de alta."""
    client = make_client(contracts=[CONTRATO_A, CONTRATO_B])
    result = await start_user_flow(hass)

    with patch_client(client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENCIALES
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "contract"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CONTRACT_ID: "9988776"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CONTRACT_ID] == "9988776"
    assert result["title"] == "EMASESA 0087654321"


async def test_seleccion_excluye_los_ya_configurados(hass: HomeAssistant) -> None:
    """El desplegable no debe ofrecer contratos que ya tienes dados de alta."""
    MockConfigEntry(
        domain=DOMAIN, unique_id=CONTRACT_ID, data={CONF_USERNAME: USERNAME}
    ).add_to_hass(hass)

    client = make_client(contracts=[CONTRATO_A, CONTRATO_B])
    result = await start_user_flow(hass)

    with patch_client(client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENCIALES
        )

    assert result["step_id"] == "contract"
    opciones = result["data_schema"].schema[CONF_CONTRACT_ID].container
    assert set(opciones) == {"9988776"}
    # Los contratos donde eres administrador de finca se marcan como tales.
    assert "administrador" in opciones["9988776"]


async def test_todos_los_contratos_ya_configurados(hass: HomeAssistant) -> None:
    """Si no queda ninguno por añadir, se aborta con un motivo claro."""
    for cid in (CONTRACT_ID, "9988776"):
        MockConfigEntry(
            domain=DOMAIN, unique_id=cid, data={CONF_USERNAME: USERNAME}
        ).add_to_hass(hass)

    client = make_client(contracts=[CONTRATO_A, CONTRATO_B])
    result = await start_user_flow(hass)

    with patch_client(client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENCIALES
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reutiliza_el_dispositivo_del_mismo_usuario(
    hass: HomeAssistant,
) -> None:
    """Segundo contrato del mismo NIF: mismo id_dispositivo, así no llega SMS."""
    MockConfigEntry(
        domain=DOMAIN,
        unique_id=CONTRACT_ID,
        data={CONF_USERNAME: USERNAME, CONF_DEVICE_ID: "dispositivo-de-confianza"},
    ).add_to_hass(hass)

    client = make_client(contracts=[CONTRATO_B])
    result = await start_user_flow(hass)

    with patch_client(client) as ctor:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENCIALES
        )
        await hass.async_block_till_done()

    assert result["data"][CONF_DEVICE_ID] == "dispositivo-de-confianza"
    assert ctor.call_args.args[3] == "dispositivo-de-confianza"


async def test_no_reutiliza_el_dispositivo_de_otro_usuario(
    hass: HomeAssistant,
) -> None:
    """Otro NIF es otra cuenta: su dispositivo de confianza no vale."""
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="1111111",
        data={CONF_USERNAME: "99999999R", CONF_DEVICE_ID: "de-otro"},
    ).add_to_hass(hass)

    result = await start_user_flow(hass)
    with patch_client(make_client()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], CREDENCIALES
        )
        await hass.async_block_till_done()

    assert result["data"][CONF_DEVICE_ID] != "de-otro"


# --------------------------------------------------------------------------- #
# Reautenticación
# --------------------------------------------------------------------------- #
def _entrada_existente(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=CONTRACT_ID,
        title="EMASESA 0012345678",
        data={
            CONF_USERNAME: USERNAME,
            CONF_PASSWORD: "la-vieja",
            CONF_DEVICE_ID: "dispositivo-1",
            CONF_CONTRACT_ID: CONTRACT_ID,
            CONF_CONTRACT_NUMBER: "0012345678",
        },
    )
    entry.add_to_hass(hass)
    return entry


async def test_reauth_guarda_la_nueva_contrasena(hass: HomeAssistant) -> None:
    entry = _entrada_existente(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    client = make_client()
    with patch_client(client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "la-nueva"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "la-nueva"
    client.register_trusted_device.assert_awaited_once()


async def test_reauth_con_la_misma_contrasena_recarga_igualmente(
    hass: HomeAssistant, _mock_setup_entry: MagicMock
) -> None:
    """El caso que dejaba la integración muerta.

    Cuando sólo caducó la confianza del dispositivo, el usuario repite la
    MISMA contraseña. `async_update_entry` no ve cambios y no dispara los
    listeners, así que la reautenticación decía "correcto" y todo seguía
    caído hasta reiniciar Home Assistant.
    """
    entry = _entrada_existente(hass)
    _mock_setup_entry.reset_mock()

    result = await entry.start_reauth_flow(hass)
    with patch_client(make_client()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "la-vieja"}
        )
        await hass.async_block_till_done()

    assert result["reason"] == "reauth_successful"
    # La prueba de que se recargó de verdad: se volvió a montar la entrada.
    assert _mock_setup_entry.call_count == 1


async def test_reauth_contrasena_incorrecta(hass: HomeAssistant) -> None:
    entry = _entrada_existente(hass)
    result = await entry.start_reauth_flow(hass)

    with patch_client(make_client(login=EmasesaAuthError("401"))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "mal"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data[CONF_PASSWORD] == "la-vieja"


async def test_reauth_error_de_red(hass: HomeAssistant) -> None:
    entry = _entrada_existente(hass)
    result = await entry.start_reauth_flow(hass)

    with patch_client(make_client(login=EmasesaError("timeout"))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "la-nueva"}
        )

    assert result["errors"] == {"base": "cannot_connect"}


async def test_reauth_pide_doble_factor(hass: HomeAssistant) -> None:
    """El dispositivo dejó de ser de confianza: hay que pasar por el PIN.

    `EmasesaTwoFactorRequired` hereda de `EmasesaError`, así que si se
    capturase después el usuario vería "no se pudo conectar" y nunca podría
    recuperar la integración.
    """
    entry = _entrada_existente(hass)
    client = make_client(login=Secuencia([EmasesaTwoFactorRequired(channel="S"), None]))

    result = await entry.start_reauth_flow(hass)
    with patch_client(client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "la-nueva"}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reauth_2fa"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"pin": "123456"}
        )
        await hass.async_block_till_done()

    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "la-nueva"


async def test_reauth_2fa_pin_incorrecto(hass: HomeAssistant) -> None:
    entry = _entrada_existente(hass)
    client = make_client(
        login=Secuencia([EmasesaTwoFactorRequired(), EmasesaTwoFactorRequired(), None])
    )

    result = await entry.start_reauth_flow(hass)
    with patch_client(client):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "la-nueva"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"pin": "000000"}
        )
        assert result["errors"] == {"base": "invalid_pin"}

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"pin": "123456"}
        )
        await hass.async_block_till_done()

    assert result["reason"] == "reauth_successful"


async def test_reauth_conserva_el_dispositivo(hass: HomeAssistant) -> None:
    """Reautenticar no puede inventar un dispositivo nuevo: sería otro SMS."""
    entry = _entrada_existente(hass)
    result = await entry.start_reauth_flow(hass)

    with patch_client(make_client()) as ctor:
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "la-nueva"}
        )
        await hass.async_block_till_done()

    assert ctor.call_args.args[3] == "dispositivo-1"
    assert entry.data[CONF_DEVICE_ID] == "dispositivo-1"


# --------------------------------------------------------------------------- #
# Opciones
# --------------------------------------------------------------------------- #
async def test_opciones_guardan_la_ubicacion_plana(hass: HomeAssistant) -> None:
    """El selector devuelve un dict; se guarda como latitude/longitude sueltos."""
    entry = _entrada_existente(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_MINUTES: 60,
            "ubicacion": {"latitude": 37.3891, "longitude": -5.9845},
            CONF_INCIDENT_RADIUS: 2500,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SCAN_MINUTES] == 60
    assert entry.options[CONF_INCIDENT_RADIUS] == 2500
    assert entry.options[CONF_LATITUDE] == 37.3891
    assert entry.options[CONF_LONGITUDE] == -5.9845
    assert "ubicacion" not in entry.options


async def test_opciones_proponen_la_ubicacion_de_home_assistant(
    hass: HomeAssistant,
) -> None:
    """Un contrato nuevo hereda la ubicación de HA hasta que se cambie."""
    entry = _entrada_existente(hass)
    hass.config.latitude = 37.3826
    hass.config.longitude = -5.9963

    result = await hass.config_entries.options.async_init(entry.entry_id)
    defaults = {
        str(key): key.default() for key in result["data_schema"].schema if key.default
    }
    assert defaults["ubicacion"] == {"latitude": 37.3826, "longitude": -5.9963}


async def test_opciones_rechazan_un_intervalo_fuera_de_rango(
    hass: HomeAssistant,
) -> None:
    """Sondear cada minuto contra una API privada no es una opción."""
    entry = _entrada_existente(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)

    with pytest.raises(Exception):  # noqa: B017 - voluptuous.MultipleInvalid
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_SCAN_MINUTES: 1,
                "ubicacion": {"latitude": 37.0, "longitude": -6.0},
                CONF_INCIDENT_RADIUS: 1000,
            },
        )
