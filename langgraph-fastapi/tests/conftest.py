import pytest


@pytest.fixture
def anyio_backend():
    """Run @pytest.mark.anyio coroutine tests on asyncio only."""
    return "asyncio"
