"""Config flow, including the in-flow pairing handshake.

The pairing dance is the whole reason this integration is worth publishing.
Doing it by hand means standing up a TLS listener on 8889, POSTing with a
crafted Host header, timing a button press inside a ~63s window, and reading
the token off a raw socket because the callback's headers break strict
parsers. All of that happens here instead.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.helpers import selector

from .api import (
    CALLBACK_PORT,
    DongleAuthError,
    DongleClient,
    DongleError,
    DonglePairingError,
    async_pair,
    local_ip_towards,
)
from .const import CONF_CERT_PEM, CONF_KEY_PEM, CONF_TOKEN, DOMAIN
from .ssl_util import InvalidCertificate, build_contexts

_LOGGER = logging.getLogger(__name__)

_MULTILINE = selector.TextSelector(
    selector.TextSelectorConfig(multiline=True, type=selector.TextSelectorType.TEXT)
)


class SamsungDongleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add one appliance."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._cert_pem: str | None = None
        self._key_pem: str | None = None

    def _existing_credentials(self) -> tuple[str, str]:
        """Reuse CA material from an appliance already configured.

        The CA is shared across every appliance, so only the first setup should
        require pasting it.
        """
        for entry in self._async_current_entries():
            cert = entry.data.get(CONF_CERT_PEM)
            key = entry.data.get(CONF_KEY_PEM)
            if cert:
                return cert, key or ""
        return "", ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        cert_default, key_default = self._existing_credentials()

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            cert_pem = user_input[CONF_CERT_PEM]
            key_pem = user_input.get(CONF_KEY_PEM, "")
            token = (user_input.get(CONF_TOKEN) or "").strip()

            try:
                client_ctx, server_ctx = await self.hass.async_add_executor_job(
                    build_contexts, cert_pem, key_pem
                )
            except InvalidCertificate as err:
                _LOGGER.debug("bad certificate material: %s", err)
                errors["base"] = "invalid_certificate"
            else:
                client = DongleClient(host, client_ctx)
                if not await client.async_probe():
                    # Most likely cause: this is the newer CoAP-DTLS generation,
                    # which has 8888 closed entirely and wants LocalThings.
                    errors["base"] = "cannot_connect"
                else:
                    self._host = host
                    self._cert_pem = cert_pem
                    self._key_pem = key_pem
                    self._client_ctx = client_ctx
                    self._server_ctx = server_ctx

                    if token:
                        return await self._async_finish(token)
                    return await self.async_step_pair()

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=user_input.get(CONF_HOST, "") if user_input else ""): str,
                vol.Required(CONF_CERT_PEM, default=cert_default): _MULTILINE,
                vol.Required(CONF_KEY_PEM, default=key_default): _MULTILINE,
                vol.Optional(CONF_TOKEN, default=""): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm, then run the handshake while the user presses the button."""
        errors: dict[str, str] = {}

        if user_input is not None:
            assert self._host is not None
            try:
                callback_ip = await self.hass.async_add_executor_job(
                    local_ip_towards, self._host
                )
                token = await async_pair(
                    self._host,
                    self._client_ctx,
                    self._server_ctx,
                    callback_ip,
                    CALLBACK_PORT,
                )
            except DonglePairingError as err:
                _LOGGER.debug("pairing failed: %s", err)
                errors["base"] = "pairing_failed"
            except DongleError as err:
                _LOGGER.debug("pairing error: %s", err)
                errors["base"] = "cannot_connect"
            else:
                return await self._async_finish(token)

        return self.async_show_form(
            step_id="pair",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={"host": self._host or ""},
        )

    async def _async_finish(self, token: str) -> ConfigFlowResult:
        assert self._host is not None
        client = DongleClient(self._host, self._client_ctx, token=token)
        try:
            device = await client.async_get_device()
        except DongleAuthError:
            return self.async_show_form(
                step_id="pair",
                data_schema=vol.Schema({}),
                errors={"base": "invalid_token"},
            )
        except DongleError:
            return self.async_show_form(
                step_id="pair",
                data_schema=vol.Schema({}),
                errors={"base": "cannot_connect"},
            )

        uuid = device.get("uuid") or self._host
        await self.async_set_unique_id(str(uuid))
        self._abort_if_unique_id_configured(updates={CONF_HOST: self._host})

        name = device.get("name") or device.get("type") or "Samsung appliance"
        return self.async_create_entry(
            title=f"{name} ({self._host})",
            data={
                CONF_HOST: self._host,
                CONF_CERT_PEM: self._cert_pem,
                CONF_KEY_PEM: self._key_pem,
                CONF_TOKEN: token,
            },
        )
