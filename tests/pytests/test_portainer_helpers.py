from __future__ import annotations

from scripts.deploy import portainer_helpers


def test_extract_webhook_token_from_nested_payload() -> None:
    payload = {
        "stack": {
            "meta": [{"foo": "bar"}, {"WebhookToken": "abc123"}],
        }
    }
    assert portainer_helpers._extract_webhook_token(payload) == "abc123"


def test_extract_ssh_hostname_user_host() -> None:
    assert portainer_helpers.extract_ssh_hostname("ronny@example.com") == "example.com"


def test_extract_ssh_hostname_plain_host() -> None:
    assert portainer_helpers.extract_ssh_hostname("example.com") == "example.com"


def test_build_portainer_webhook_urls_from_token() -> None:
    urls = portainer_helpers.build_portainer_webhook_urls_from_token(
        host="ronny@example.com",
        https_port=9943,
        webhook_token="tok",
    )
    assert urls == [
        "https://example.com:9943/api/stacks/webhooks/tok",
        "https://example.com:9943/api/webhooks/tok",
    ]
