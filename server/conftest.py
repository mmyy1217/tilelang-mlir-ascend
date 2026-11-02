"""The production ASGI service uses asyncio tasks and runs under uvicorn."""

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"
