import hashlib
import math

import pytest

from service.canonical import (
    ALLOW_NAN,
    ENSURE_ASCII,
    SEPARATORS,
    SORT_KEYS,
    CanonicalEncodingError,
    canonical_encode,
    canonical_sha256,
)


def test_flags_are_the_specced_defaults():
    assert SEPARATORS == (",", ":")
    assert ENSURE_ASCII is True
    assert SORT_KEYS is False
    assert ALLOW_NAN is False


def test_no_whitespace_compact_separators():
    assert canonical_encode({"a": 1, "b": [1, 2]}) == b'{"a":1,"b":[1,2]}'


def test_key_order_preserved_not_sorted():
    assert canonical_encode({"b": 1, "a": 2}) == b'{"b":1,"a":2}'


def test_determinism():
    obj = {"x": [1, 2, 3], "y": {"z": "é"}}
    assert canonical_encode(obj) == canonical_encode(obj)


def test_unicode_escaped_ascii():
    assert canonical_encode({"s": "é"}) == b'{"s":"\\u00e9"}'


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_nan_inf_rejected(bad):
    with pytest.raises(CanonicalEncodingError):
        canonical_encode({"v": bad})


def test_non_serializable_rejected():
    with pytest.raises(CanonicalEncodingError):
        canonical_encode({"v": object()})


def test_canonical_sha256():
    data = canonical_encode({"a": 1})
    assert canonical_sha256(data) == hashlib.sha256(data).hexdigest()
