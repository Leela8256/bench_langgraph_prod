import io
import os

import pytest
from fastapi import UploadFile

from service.errors import ServiceError
from service.upload import CHUNK_BYTES, persist_upload


def make_upload(data: bytes, filename: str = "f.bin") -> UploadFile:
    return UploadFile(filename=filename, file=io.BytesIO(data))


@pytest.mark.anyio
async def test_writes_file_and_reports_size(tmp_path):
    data = b"hello world"
    path, size = await persist_upload(make_upload(data), str(tmp_path), 1000, "rid")
    assert size == len(data)
    with open(path, "rb") as fh:
        assert fh.read() == data
    os.unlink(path)


@pytest.mark.anyio
async def test_chunked_write_handles_multi_chunk_payload(tmp_path):
    # Larger than one 1MB chunk, so the copy loop iterates.
    data = b"x" * (CHUNK_BYTES * 2 + 17)
    path, size = await persist_upload(
        make_upload(data), str(tmp_path), CHUNK_BYTES * 4, "rid"
    )
    assert size == len(data)
    assert os.path.getsize(path) == len(data)
    os.unlink(path)


@pytest.mark.anyio
async def test_limit_enforced_mid_copy_not_after(tmp_path):
    limit = CHUNK_BYTES  # exceeded on the second chunk
    data = b"y" * (CHUNK_BYTES * 3)
    with pytest.raises(ServiceError) as exc:
        await persist_upload(make_upload(data), str(tmp_path), limit, "rid")
    assert exc.value.code == "payload_too_large"
    assert exc.value.status_code == 413
    # Failed at the limit: never wrote the whole payload, and cleaned up.
    assert list(tmp_path.iterdir()) == []


@pytest.mark.anyio
async def test_exactly_at_limit_is_accepted(tmp_path):
    data = b"z" * 100
    path, size = await persist_upload(make_upload(data), str(tmp_path), 100, "rid")
    assert size == 100
    os.unlink(path)


@pytest.mark.anyio
async def test_empty_file_raises_empty_input(tmp_path):
    with pytest.raises(ServiceError) as exc:
        await persist_upload(make_upload(b""), str(tmp_path), 1000, "rid")
    assert exc.value.code == "empty_input"
    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_no_partial_file_left_behind_on_error(tmp_path):
    for data, limit in ((b"", 1000), (b"a" * 500, 10)):
        with pytest.raises(ServiceError):
            await persist_upload(make_upload(data), str(tmp_path), limit, "rid")
        assert list(tmp_path.iterdir()) == []
