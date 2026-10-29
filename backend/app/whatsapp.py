"""Opt-in sender for one allowlisted WhatsApp Cloud API test recipient."""

import json
import math
import os
import re
from dataclasses import dataclass, field
from typing import Mapping

import httpx

from . import config as _config  # noqa: F401 - load backend/.env


GRAPH_API_ROOT = "https://graph.facebook.com"
_GRAPH_VERSION = re.compile(r"^v\d+\.\d+$")
_PHONE_NUMBER_ID = re.compile(r"^\d{5,30}$")
_TEMPLATE_NAME = re.compile(r"^[a-z0-9_]{1,512}$")
_LANGUAGE_CODE = re.compile(r"^[a-z]{2,3}(?:_[A-Z]{2})?$")
_E164 = re.compile(r"^\+[1-9]\d{7,14}$")
SAFE_SEND_ERROR_CODES = frozenset({
    "meta_auth_invalid_or_expired", "meta_template_rejected", "meta_rate_limited_no_retry",
    "meta_request_rejected_no_retry", "meta_timeout_outcome_unknown", "meta_network_outcome_unknown",
    "meta_server_error_outcome_unknown", "meta_response_missing_id_outcome_unknown",
    "provider_outcome_unknown_no_retry", "provider_rejected_no_retry",
})


class WhatsAppConfigurationError(Exception):
    """Safe configuration failure. Messages never include configured values."""


class WhatsAppSendError(Exception):
    """Safe provider failure. Messages never include credentials or phone values."""

    def __init__(self, message: str, *, outcome_unknown: bool = False, reason_code: str | None = None):
        super().__init__(message)
        self.outcome_unknown = outcome_unknown
        self.reason_code = reason_code if reason_code in SAFE_SEND_ERROR_CODES else (
            "provider_outcome_unknown_no_retry" if outcome_unknown else "provider_rejected_no_retry"
        )


@dataclass(frozen=True)
class WhatsAppTestConfig:
    access_token: str = field(repr=False)
    phone_number_id: str
    graph_api_version: str
    mode: str
    recipient: str = field(repr=False)
    allowed_recipients: frozenset[str] = field(repr=False)
    recipient_verified: bool
    template_name: str
    language_code: str
    components: tuple[dict, ...] = field(repr=False)
    timeout_seconds: float = 15.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "WhatsAppTestConfig":
        values = os.environ if env is None else env
        if values.get("WHATSAPP_MODE", "").strip().lower() != "test":
            raise WhatsAppConfigurationError("WHATSAPP_MODE must be set to test.")

        required = (
            "WHATSAPP_ACCESS_TOKEN",
            "WHATSAPP_PHONE_NUMBER_ID",
            "WHATSAPP_GRAPH_API_VERSION",
            "WHATSAPP_TEST_RECIPIENT",
            "WHATSAPP_TEST_ALLOWED_RECIPIENTS",
            "WHATSAPP_TEST_RECIPIENT_VERIFIED",
            "WHATSAPP_TEST_TEMPLATE_NAME",
            "WHATSAPP_TEST_TEMPLATE_LANGUAGE",
            "WHATSAPP_TEST_TEMPLATE_COMPONENTS",
        )
        missing = [key for key in required if not values.get(key, "").strip()]
        if missing:
            raise WhatsAppConfigurationError("Missing required configuration: " + ", ".join(missing))

        if values["WHATSAPP_TEST_RECIPIENT_VERIFIED"].strip().lower() != "true":
            raise WhatsAppConfigurationError("Set WHATSAPP_TEST_RECIPIENT_VERIFIED=true only for your verified test number.")

        recipient = _normalize_e164(values["WHATSAPP_TEST_RECIPIENT"], "WHATSAPP_TEST_RECIPIENT")
        allowed = frozenset(
            _normalize_e164(number.strip(), "WHATSAPP_TEST_ALLOWED_RECIPIENTS")
            for number in values["WHATSAPP_TEST_ALLOWED_RECIPIENTS"].split(",")
            if number.strip()
        )
        if not allowed or recipient not in allowed:
            raise WhatsAppConfigurationError("The configured test recipient is not in WHATSAPP_TEST_ALLOWED_RECIPIENTS.")

        phone_number_id = values["WHATSAPP_PHONE_NUMBER_ID"].strip()
        if not _PHONE_NUMBER_ID.fullmatch(phone_number_id):
            raise WhatsAppConfigurationError("WHATSAPP_PHONE_NUMBER_ID must be a numeric Meta phone number ID.")

        graph_api_version = values["WHATSAPP_GRAPH_API_VERSION"].strip()
        if not _GRAPH_VERSION.fullmatch(graph_api_version):
            raise WhatsAppConfigurationError("WHATSAPP_GRAPH_API_VERSION must use the form vN.N.")

        template_name = values["WHATSAPP_TEST_TEMPLATE_NAME"].strip()
        if not _TEMPLATE_NAME.fullmatch(template_name):
            raise WhatsAppConfigurationError("WHATSAPP_TEST_TEMPLATE_NAME has an invalid format.")

        language_code = values["WHATSAPP_TEST_TEMPLATE_LANGUAGE"].strip()
        if not _LANGUAGE_CODE.fullmatch(language_code):
            raise WhatsAppConfigurationError("WHATSAPP_TEST_TEMPLATE_LANGUAGE has an invalid format.")

        try:
            components = json.loads(values["WHATSAPP_TEST_TEMPLATE_COMPONENTS"])
        except json.JSONDecodeError:
            raise WhatsAppConfigurationError("WHATSAPP_TEST_TEMPLATE_COMPONENTS must be a JSON array.") from None
        if not isinstance(components, list) or any(not isinstance(component, dict) for component in components):
            raise WhatsAppConfigurationError("WHATSAPP_TEST_TEMPLATE_COMPONENTS must be a JSON array of component objects.")

        try:
            timeout_seconds = float(values.get("WHATSAPP_REQUEST_TIMEOUT_SECONDS", "15"))
        except ValueError:
            raise WhatsAppConfigurationError("WHATSAPP_REQUEST_TIMEOUT_SECONDS must be a positive number.") from None
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0 or timeout_seconds > 120:
            raise WhatsAppConfigurationError("WHATSAPP_REQUEST_TIMEOUT_SECONDS must be between 0 and 120.")

        access_token = values["WHATSAPP_ACCESS_TOKEN"].strip()
        return cls(
            access_token=access_token,
            phone_number_id=phone_number_id,
            graph_api_version=graph_api_version,
            mode="test",
            recipient=recipient,
            allowed_recipients=allowed,
            recipient_verified=True,
            template_name=template_name,
            language_code=language_code,
            components=tuple(components),
            timeout_seconds=timeout_seconds,
        )


def _normalize_e164(value: str, setting_name: str) -> str:
    normalized = value.strip()
    if not _E164.fullmatch(normalized):
        raise WhatsAppConfigurationError(f"{setting_name} must be an E.164 phone number.")
    return normalized


def send_test_template(config: WhatsAppTestConfig, http_client: httpx.Client | None = None) -> str:
    """Send exactly one approved template to the configured allowlisted test number."""
    if config.mode != "test":
        raise WhatsAppConfigurationError("WhatsApp sender is restricted to WHATSAPP_MODE=test.")
    if not config.recipient_verified:
        raise WhatsAppConfigurationError("The test recipient must be verified before sending.")
    if config.recipient not in config.allowed_recipients:
        raise WhatsAppConfigurationError("The test recipient is not allowlisted.")

    url = f"{GRAPH_API_ROOT}/{config.graph_api_version}/{config.phone_number_id}/messages"
    template = {
        "name": config.template_name,
        "language": {"code": config.language_code},
    }
    if config.components:
        template["components"] = list(config.components)
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": config.recipient[1:],
        "type": "template",
        "template": template,
    }
    headers = {
        "Authorization": f"Bearer {config.access_token}",
        "Content-Type": "application/json",
    }

    own_client = http_client is None
    client = http_client or httpx.Client(follow_redirects=False)
    try:
        response = client.post(url, headers=headers, json=payload, timeout=config.timeout_seconds, follow_redirects=False)
    except httpx.TimeoutException:
        raise WhatsAppSendError("Meta request timed out; delivery status is unknown. Reconcile before any manual retry.", outcome_unknown=True, reason_code="meta_timeout_outcome_unknown") from None
    except httpx.RequestError:
        raise WhatsAppSendError("Meta network request failed; its outcome must be reconciled.", outcome_unknown=True, reason_code="meta_network_outcome_unknown") from None
    finally:
        if own_client:
            client.close()

    if not response.is_success:
        _raise_provider_error(response)

    try:
        result = response.json()
        messages = result.get("messages") if isinstance(result, dict) else None
        message_id = messages[0].get("id") if isinstance(messages, list) and messages and isinstance(messages[0], dict) else None
    except (ValueError, TypeError, AttributeError):
        message_id = None
    if not isinstance(message_id, str) or not message_id:
        raise WhatsAppSendError("Meta returned success without a message ID.", outcome_unknown=True, reason_code="meta_response_missing_id_outcome_unknown")
    return message_id


def _raise_provider_error(response: httpx.Response) -> None:
    provider_code = None
    try:
        body = response.json()
        error = body.get("error", {}) if isinstance(body, dict) else {}
        provider_code = error.get("code") if isinstance(error, dict) else None
    except (ValueError, TypeError):
        pass

    if response.status_code == 401 or provider_code == 190:
        message = "Meta rejected the access token; it may be invalid or expired."
        reason = "meta_auth_invalid_or_expired"
    elif response.status_code == 429:
        message = "Meta rate-limited the request. Wait before trying again."
        reason = "meta_rate_limited_no_retry"
    elif response.status_code >= 500:
        message = f"Meta's Graph API is temporarily unavailable (HTTP {response.status_code})."
        reason = "meta_server_error_outcome_unknown"
    elif response.status_code == 408:
        message = "Meta timed out; the outcome must be reconciled."
        reason = "meta_timeout_outcome_unknown"
    elif response.status_code in (400, 403) and provider_code in (10, 100, 131008, 132000, 132001):
        message = "Meta rejected the template or its configured parameters. Check the template name, language, and components in WhatsApp Manager."
        reason = "meta_template_rejected"
    else:
        message = f"Meta rejected the request (HTTP {response.status_code}). Check the WhatsApp test configuration."
        reason = "meta_request_rejected_no_retry"
    # Do not attach raw provider JSON: Meta errors may echo recipients or request data.
    raise WhatsAppSendError(message, outcome_unknown=response.status_code >= 500 or response.status_code == 408, reason_code=reason)
