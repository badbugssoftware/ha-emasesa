"""Flujo de configuración para EMASESA."""
from __future__ import annotations

import logging
import uuid
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    EmasesaAuthError,
    EmasesaClient,
    EmasesaError,
    EmasesaTwoFactorRequired,
)
from .const import (
    CONF_CONTRACT_ID,
    CONF_CONTRACT_NUMBER,
    CONF_DEVICE_ID,
    CONF_INCIDENT_RADIUS,
    CONF_PASSWORD,
    CONF_SCAN_MINUTES,
    CONF_SUPPLY_ADDRESS,
    CONF_USERNAME,
    DEFAULT_INCIDENT_RADIUS,
    DEFAULT_SCAN_MINUTES,
    DOMAIN,
    MAX_INCIDENT_RADIUS,
    MIN_INCIDENT_RADIUS,
    MAX_SCAN_MINUTES,
    MIN_SCAN_MINUTES,
)

_LOGGER = logging.getLogger(__name__)


def _new_device_id() -> str:
    """Genera un id de dispositivo (UUID completo), como hace la app."""
    return str(uuid.uuid4())


class EmasesaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow: usuario/contraseña, doble factor y selección de contrato."""

    VERSION = 1

    def __init__(self) -> None:
        self._client: EmasesaClient | None = None
        self._data: dict[str, Any] = {}
        self._contracts: list[dict[str, Any]] = []
        self._reauth_password: str | None = None
        self._reauth_device_id: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            device_id = _new_device_id()
            self._data = {
                CONF_USERNAME: user_input[CONF_USERNAME].strip().upper(),
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                CONF_DEVICE_ID: device_id,
            }
            self._client = EmasesaClient(
                async_get_clientsession(self.hass),
                self._data[CONF_USERNAME],
                self._data[CONF_PASSWORD],
                device_id,
            )
            try:
                await self._client.login()
            except EmasesaTwoFactorRequired:
                return await self.async_step_2fa()
            except EmasesaAuthError:
                errors["base"] = "invalid_auth"
            except EmasesaError:
                errors["base"] = "cannot_connect"
            else:
                return await self._after_login()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_2fa(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Paso de doble factor: introducir el código recibido por SMS/email."""
        errors: dict[str, str] = {}
        assert self._client is not None
        if user_input is not None:
            try:
                await self._client.login(pin=user_input["pin"].strip())
            except EmasesaTwoFactorRequired:
                errors["base"] = "invalid_pin"
            except EmasesaAuthError:
                errors["base"] = "invalid_auth"
            except EmasesaError:
                errors["base"] = "cannot_connect"
            else:
                return await self._after_login()

        return self.async_show_form(
            step_id="2fa",
            data_schema=vol.Schema({vol.Required("pin"): str}),
            errors=errors,
        )

    async def _after_login(self) -> ConfigFlowResult:
        """Tras autenticar: registra el dispositivo, obtiene contratos y decide."""
        assert self._client is not None
        # Marca el dispositivo como de confianza para que el coordinator (y los
        # arranques posteriores) NO vuelvan a exigir doble factor.
        try:
            await self._client.register_trusted_device()
        except EmasesaError:
            _LOGGER.warning(
                "No se pudo registrar el dispositivo de confianza", exc_info=True
            )
        try:
            self._contracts = await self._client.get_contracts()
        except EmasesaError:
            return self.async_abort(reason="cannot_connect")

        if not self._contracts:
            return self.async_abort(reason="no_contracts")
        if len(self._contracts) == 1:
            return await self._create_entry(self._contracts[0])
        return await self.async_step_contract()

    async def async_step_contract(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Selección de contrato cuando hay varios suministros."""
        if user_input is not None:
            chosen = next(
                c
                for c in self._contracts
                if str(c.get("contratos_id")) == user_input[CONF_CONTRACT_ID]
            )
            return await self._create_entry(chosen)

        options = {
            str(c.get("contratos_id")): (
                f"{c.get('numero_contrato') or c.get('contratos_id')} "
                f"— {c.get('direccion_suministro', '')}"
            )
            for c in self._contracts
        }
        return self.async_show_form(
            step_id="contract",
            data_schema=vol.Schema({vol.Required(CONF_CONTRACT_ID): vol.In(options)}),
        )

    async def _create_entry(self, contract: dict[str, Any]) -> ConfigFlowResult:
        contract_id = str(contract.get("contratos_id"))
        await self.async_set_unique_id(contract_id)
        self._abort_if_unique_id_configured()

        address = contract.get("direccion_suministro", "")
        number = contract.get("numero_contrato") or contract_id
        return self.async_create_entry(
            title=f"EMASESA {number}",
            data={
                **self._data,
                CONF_CONTRACT_ID: contract_id,
                CONF_CONTRACT_NUMBER: number,
                CONF_SUPPLY_ADDRESS: address,
            },
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        self._data = dict(entry_data)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            device_id = self._data.get(CONF_DEVICE_ID) or _new_device_id()
            client = EmasesaClient(
                async_get_clientsession(self.hass),
                self._data[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                device_id,
            )
            self._client = client
            self._reauth_password = user_input[CONF_PASSWORD]
            self._reauth_device_id = device_id
            try:
                await client.login()
            except EmasesaTwoFactorRequired:
                # El dispositivo dejó de ser de confianza: pedir el PIN en vez
                # de fallar como "cannot_connect" (EmasesaTwoFactorRequired
                # hereda de EmasesaError, así que debe capturarse ANTES).
                return await self.async_step_reauth_2fa()
            except EmasesaAuthError:
                errors["base"] = "invalid_auth"
            except EmasesaError:
                errors["base"] = "cannot_connect"
            else:
                return await self._finish_reauth()

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )

    async def async_step_reauth_2fa(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Doble factor durante la reautenticación."""
        errors: dict[str, str] = {}
        assert self._client is not None
        if user_input is not None:
            try:
                await self._client.login(pin=user_input["pin"].strip())
            except EmasesaTwoFactorRequired:
                errors["base"] = "invalid_pin"
            except EmasesaAuthError:
                errors["base"] = "invalid_auth"
            except EmasesaError:
                errors["base"] = "cannot_connect"
            else:
                return await self._finish_reauth()

        return self.async_show_form(
            step_id="reauth_2fa",
            data_schema=vol.Schema({vol.Required("pin"): str}),
            errors=errors,
        )

    async def _finish_reauth(self) -> ConfigFlowResult:
        """Registra el dispositivo de confianza y guarda las credenciales."""
        assert self._client is not None
        # Sin esto, el siguiente ciclo del coordinator volvería a pedir 2FA.
        try:
            await self._client.register_trusted_device()
        except EmasesaError:
            _LOGGER.warning(
                "No se pudo registrar el dispositivo de confianza en el reauth",
                exc_info=True,
            )
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        assert entry is not None
        # El update-listener (add_update_listener en __init__) ya recarga la
        # entrada al cambiar los datos: no llamamos a async_reload (doble carga).
        self.hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_PASSWORD: self._reauth_password,
                CONF_DEVICE_ID: self._reauth_device_id,
            },
        )
        return self.async_abort(reason="reauth_successful")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return EmasesaOptionsFlow()


class EmasesaOptionsFlow(OptionsFlow):
    """Permite ajustar el intervalo de sondeo."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(
            CONF_SCAN_MINUTES, DEFAULT_SCAN_MINUTES
        )
        radius = self.config_entry.options.get(
            CONF_INCIDENT_RADIUS, DEFAULT_INCIDENT_RADIUS
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_MINUTES, default=current): vol.All(
                        vol.Coerce(int), vol.Range(min=MIN_SCAN_MINUTES, max=MAX_SCAN_MINUTES)
                    ),
                    vol.Required(CONF_INCIDENT_RADIUS, default=radius): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_INCIDENT_RADIUS, max=MAX_INCIDENT_RADIUS),
                    ),
                }
            ),
        )
