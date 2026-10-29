import json

import httpx
import pytest

from app.whatsapp import (
    GRAPH_API_ROOT,
    WhatsAppConfigurationError,
    WhatsAppSendError,
    WhatsAppTestConfig,
    send_test_template,
)
from scripts.send_whatsapp_test import main


TEST_RECIPIENT = "+919876543210"
TEST_COMPONENTS = [{
    "type": "body",
    "parameters": [
        {"type": "text", "text": "LocalGrowth Demo"},
        {"type": "text", "text": "10%"},
    ],
}]


def env_values(**updates):
    values = {
        "WHATSAPP_MODE": "test",
        "WHATSAPP_ACCESS_TOKEN": "mock_access_token_never_for_real_use",
        "WHATSAPP_PHONE_NUMBER_ID": "123456789012345",
        "WHATSAPP_GRAPH_API_VERSION": "v26.0",
        "WHATSAPP_TEST_RECIPIENT": TEST_RECIPIENT,
        "WHATSAPP_TEST_ALLOWED_RECIPIENTS": TEST_RECIPIENT,
        "WHATSAPP_TEST_RECIPIENT_VERIFIED": "true",
        "WHATSAPP_TEST_TEMPLATE_NAME": "hello_world",
        "WHATSAPP_TEST_TEMPLATE_LANGUAGE": "en_US",
        "WHATSAPP_TEST_TEMPLATE_COMPONENTS": json.dumps(TEST_COMPONENTS),
    }
    values.update(updates)
    return values


def test_send_uses_meta_graph_template_endpoint_and_configured_components():
    config = WhatsAppTestConfig.from_env(env_values())
    observed = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["url"] = str(request.url)
        observed["authorization"] = request.headers["Authorization"]
        observed["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"messaging_product": "whatsapp", "messages": [{"id": "wamid.mock-123"}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        message_id = send_test_template(config, http_client=client)

    assert message_id == "wamid.mock-123"
    assert observed["url"] == f"{GRAPH_API_ROOT}/v26.0/123456789012345/messages"
    assert observed["authorization"] == "Bearer mock_access_token_never_for_real_use"
    assert observed["payload"] == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": "919876543210",
        "type": "template",
        "template": {
            "name": "hello_world",
            "language": {"code": "en_US"},
            "components": TEST_COMPONENTS,
        },
    }


def test_no_send_flag_is_dry_run_and_never_calls_provider(capsys):
    def unexpected_sender(_config):
        raise AssertionError("sender must not be called without --send")

    assert main([], sender=unexpected_sender) == 0
    captured = capsys.readouterr()
    assert "Dry run" in captured.out
    assert "mock_access_token" not in captured.out
    assert TEST_RECIPIENT not in captured.out


def test_cli_send_prints_message_id_but_not_credentials_or_recipient(monkeypatch, capsys):
    for name, value in env_values().items():
        monkeypatch.setenv(name, value)
    assert main(["--send"], sender=lambda _config: "wamid.mock-456") == 0
    captured = capsys.readouterr()
    assert "wamid.mock-456" in captured.out
    assert "mock_access_token" not in captured.out
    assert TEST_RECIPIENT not in captured.out


def test_recipient_must_be_verified_and_explicitly_allowlisted():
    with pytest.raises(WhatsAppConfigurationError, match="verified test number"):
        WhatsAppTestConfig.from_env(env_values(WHATSAPP_TEST_RECIPIENT_VERIFIED="false"))
    with pytest.raises(WhatsAppConfigurationError, match="not in WHATSAPP_TEST_ALLOWED_RECIPIENTS"):
        WhatsAppTestConfig.from_env(env_values(WHATSAPP_TEST_ALLOWED_RECIPIENTS="+919876543211"))


def test_live_mode_is_rejected():
    with pytest.raises(WhatsAppConfigurationError, match="WHATSAPP_MODE must be set to test"):
        WhatsAppTestConfig.from_env(env_values(WHATSAPP_MODE="live"))


def test_expired_or_invalid_token_error_is_safe():
    config = WhatsAppTestConfig.from_env(env_values())

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={
            "error": {
                "message": f"Invalid OAuth access token for recipient {TEST_RECIPIENT}",
                "type": "OAuthException",
                "code": 190,
            },
        })

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(WhatsAppSendError) as exc_info:
            send_test_template(config, http_client=client)
    message = str(exc_info.value)
    assert "invalid or expired" in message
    assert TEST_RECIPIENT not in message
    assert "mock_access_token" not in message


def test_timeout_is_safe_and_does_not_include_recipient():
    config = WhatsAppTestConfig.from_env(env_values())

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("request timed out")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(WhatsAppSendError, match="delivery status is unknown") as exc_info:
            send_test_template(config, http_client=client)
    assert TEST_RECIPIENT not in str(exc_info.value)


def test_provider_error_does_not_echo_raw_meta_body():
    config = WhatsAppTestConfig.from_env(env_values())

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={
            "error": {"message": f"Rejected {TEST_RECIPIENT}; token=mock_access_token", "code": 132001}
        })

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(WhatsAppSendError) as exc_info:
            send_test_template(config, http_client=client)
    assert TEST_RECIPIENT not in str(exc_info.value)
    assert "mock_access_token" not in str(exc_info.value)
    assert "template or its configured parameters" in str(exc_info.value)


def test_template_parameter_json_must_be_component_array():
    with pytest.raises(WhatsAppConfigurationError, match="JSON array"):
        WhatsAppTestConfig.from_env(env_values(WHATSAPP_TEST_TEMPLATE_COMPONENTS="{}"))
