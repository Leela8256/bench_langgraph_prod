import pytest

from service.config import Settings, SettingsError, configure_runtime


def test_defaults():
    s = Settings.from_env({})
    assert s.executor_workers == 4
    assert s.max_inflight_requests == 8
    assert s.request_timeout_seconds == 300
    assert s.torch_threads == 1
    assert s.torch_interop_threads == 1
    assert s.pipelines == ["document-v1"]
    assert s.port == 8100
    assert s.uvicorn_workers == 1
    assert s.benchmark_mode is False


def test_env_overrides():
    s = Settings.from_env(
        {
            "EXECUTOR_WORKERS": "2",
            "PIPELINES": "mock-v1, other-v1",
            "MAX_UPLOAD_BYTES": "1000",
            "PORT": "9000",
            "BENCHMARK_MODE": "true",
        }
    )
    assert s.executor_workers == 2
    assert s.pipelines == ["mock-v1", "other-v1"]
    assert s.max_upload_bytes == 1000
    assert s.port == 9000
    assert s.benchmark_mode is True


@pytest.mark.parametrize(
    "env",
    [
        {"EXECUTOR_WORKERS": "abc"},
        {"PORT": "not-a-port"},
        {"BENCHMARK_MODE": "maybe"},
        {"PIPELINES": " , "},
    ],
)
def test_invalid_env_raises(env):
    with pytest.raises(SettingsError):
        Settings.from_env(env)


def test_configure_runtime_pins(monkeypatch):
    import os

    s = Settings.from_env({"TORCH_THREADS": "1"})
    pins = configure_runtime(s)
    assert pins == {
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "TOKENIZERS_PARALLELISM": "false",
    }
    for key, value in pins.items():
        assert os.environ[key] == value
