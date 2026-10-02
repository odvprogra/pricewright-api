from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from pricewright.api.app import create_app


@pytest.fixture
def app() -> FastAPI:
    return create_app(title="test")


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
