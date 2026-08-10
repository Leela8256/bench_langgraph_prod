import json

import pytest

from service.errors import ERROR_STATUS, ServiceError, error_body, error_response

EXPECTED = {
    "unknown_pipeline": 404,
    "server_not_ready": 503,
    "invalid_options": 400,
    "empty_input": 400,
    "unsupported_media_type": 415,
    "payload_too_large": 413,
    "processing_timeout": 504,
    "processing_failed": 500,
    "canonical_encoding_failed": 500,
}


def test_full_code_to_status_mapping():
    assert ERROR_STATUS == EXPECTED


@pytest.mark.parametrize("code,status", sorted(EXPECTED.items()))
def test_response_status_and_body(code, status):
    err = ServiceError(code, "boom", "rid-1")
    resp = error_response(err)
    assert resp.status_code == status
    assert json.loads(resp.body) == {
        "error": {"code": code, "message": "boom", "request_id": "rid-1"}
    }


def test_body_shape_with_null_request_id():
    body = json.loads(error_body("empty_input", "msg", None))
    assert body == {
        "error": {"code": "empty_input", "message": "msg", "request_id": None}
    }


def test_unknown_code_rejected():
    with pytest.raises(ValueError):
        ServiceError("not_a_code", "msg")
