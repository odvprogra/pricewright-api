"""The SMTP mailer against a real mail server: Mailpit, the one docker compose runs (ADR-0028)."""

import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from testcontainers.core.container import DockerContainer

from pricewright.infrastructure.mailer import SmtpMailer, SmtpSettings
from tests.unit.test_mailer import EMAIL, TEMPLATES

pytestmark = pytest.mark.integration

# Same image as docker-compose.yml.
MAILPIT_IMAGE = "axllent/mailpit:v1.31.5"
READY_TIMEOUT_S = 60


@dataclass(frozen=True)
class Mailpit:
    host: str
    smtp_port: int
    api: str

    def messages(self) -> list[dict[str, Any]]:
        listed: list[dict[str, Any]] = httpx.get(f"{self.api}/api/v1/messages").json()["messages"]
        return listed

    def message(self, message_id: str) -> dict[str, Any]:
        found: dict[str, Any] = httpx.get(f"{self.api}/api/v1/message/{message_id}").json()
        return found


def _until_ready(api: str) -> None:
    """Polls Mailpit's readiness endpoint: a condition, with a deadline only against hanging."""
    deadline = time.monotonic() + READY_TIMEOUT_S
    while True:
        try:
            if httpx.get(f"{api}/readyz", timeout=2).status_code == 200:
                return
        except httpx.TransportError:
            pass
        if time.monotonic() > deadline:
            raise TimeoutError("Mailpit never became ready")
        time.sleep(0.2)


@pytest.fixture(scope="module")
def mailpit() -> Iterator[Mailpit]:
    with DockerContainer(MAILPIT_IMAGE).with_exposed_ports(1025, 8025) as container:
        host = container.get_container_host_ip()
        api = f"http://{host}:{container.get_exposed_port(8025)}"
        _until_ready(api)
        yield Mailpit(host, int(container.get_exposed_port(1025)), api)


async def test_mailer_delivers_the_email_with_its_pdf_to_the_mail_server(mailpit: Mailpit) -> None:
    settings = SmtpSettings(
        host=mailpit.host, port=mailpit.smtp_port, sender="quotes@pricewright.example"
    )

    await SmtpMailer(settings, TEMPLATES).send(EMAIL)

    [summary] = mailpit.messages()
    assert summary["Subject"] == "Quotation NF-2026-000001"
    assert summary["From"] == {"Name": "Northfield Supply", "Address": "quotes@pricewright.example"}
    assert [to["Address"] for to in summary["To"]] == ["purchasing@acme.example"]
    assert [reply["Address"] for reply in summary["ReplyTo"]] == ["jordan@northfield.example"]
    assert summary["MessageID"] == "0199a1b2-event@pricewright.example"
    message = mailpit.message(summary["ID"])
    [pdf] = message["Attachments"]
    assert (pdf["FileName"], pdf["ContentType"]) == ("NF-2026-000001.pdf", "application/pdf")
    assert message["Text"].startswith("Hello Acme & Sons")
