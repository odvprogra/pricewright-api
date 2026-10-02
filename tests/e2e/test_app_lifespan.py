from pricewright.api.app import create_app


async def test_shutdown_hooks_run_when_the_app_stops() -> None:
    stopped: list[str] = []

    async def close_pool() -> None:
        stopped.append("pool")

    app = create_app(title="test", on_shutdown=[close_pool])

    async with app.router.lifespan_context(app):
        assert stopped == []

    assert stopped == ["pool"]
