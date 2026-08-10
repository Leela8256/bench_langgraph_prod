import pytest

from pipelines.mock import build_mock_pipeline
from service.app import create_app
from service.config import Settings


@pytest.fixture
def temp_dir(tmp_path):
    d = tmp_path / "uploads"
    d.mkdir()
    return d


@pytest.fixture
def settings(temp_dir):
    return Settings.from_env(
        {
            "PIPELINES": "mock-v1",
            "TEMP_DIRECTORY": str(temp_dir),
            "MAX_UPLOAD_BYTES": "10000",
        }
    )


@pytest.fixture
def builders():
    return {"mock-v1": build_mock_pipeline}


@pytest.fixture
def app(settings, builders):
    return create_app(settings=settings, builders=builders)
