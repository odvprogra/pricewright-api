import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from pricewright.infrastructure.database import (
    create_engine,
    create_session_factory,
    ping,
)

pytestmark = pytest.mark.integration


async def test_ping_succeeds_against_a_running_database(engine: AsyncEngine) -> None:
    await ping(engine)


async def test_ping_fails_when_the_database_is_unreachable() -> None:
    engine = create_engine("postgresql+asyncpg://app:app@127.0.0.1:1/missing")

    with pytest.raises(OSError):  # noqa: PT011 - the driver's message is platform-specific
        await ping(engine)

    await engine.dispose()


async def test_session_factory_opens_working_sessions(engine: AsyncEngine) -> None:
    sessions = create_session_factory(engine)

    async with sessions() as session:
        result = await session.execute(text("SELECT 1"))

    assert result.scalar_one() == 1
