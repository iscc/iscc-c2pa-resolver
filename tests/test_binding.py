"""Tests for decoding `io.iscc.v0` soft binding values, using the IEP-0020 test vectors."""

import base64
import json

import iscc_core as ic
import pytest

from iscc_c2pa_resolver.binding import MAX_VALUE_LENGTH, decode_base64, decode_value, is_searchable
from tests.conftest import CONTENT, DATA, DATA_CODE, INSTANCE, META, VECTOR_1, encode


def test_decode_value_vector_1_drops_meta_code():
    assert decode_value(VECTOR_1) == [CONTENT, DATA_CODE, INSTANCE]


def test_decode_value_of_a_signed_manifest():
    """The landing page sends the soft binding of a manifest as base64 of its bytes; the demo app signed this one."""
    store = json.loads((DATA / "store-iscc-untrusted.json").read_text(encoding="utf-8"))
    manifest = store["manifests"][store["active_manifest"]]
    (assertion,) = [a for a in manifest["assertions"] if a["label"] == "c2pa.soft-binding"]
    value = base64.b64encode(bytes(assertion["data"]["blocks"][0]["value"])).decode()
    assert [ic.iscc_decode(unit)[0] for unit in decode_value(value)] == [ic.MT.CONTENT, ic.MT.DATA, ic.MT.INSTANCE]


def test_decode_value_matches_reference_encoding():
    assert encode([META, CONTENT, DATA_CODE, INSTANCE]) == VECTOR_1


@pytest.mark.parametrize(
    "variant",
    [
        VECTOR_1.rstrip("="),  # unpadded
        VECTOR_1.replace("+", "-").replace("/", "_"),  # URL-safe alphabet
        VECTOR_1.replace("+", " "),  # '+' decoded as space by a client that did not percent-encode
        f"  {VECTOR_1}\n",  # surrounding whitespace
    ],
)
def test_decode_value_is_lenient(variant):
    assert decode_value(variant) == [CONTENT, DATA_CODE, INSTANCE]


def test_decode_value_removes_duplicate_units():
    assert decode_value(encode([DATA_CODE, INSTANCE, DATA_CODE])) == [DATA_CODE, INSTANCE]


def test_decode_base64_restores_padding():
    assert decode_base64("AAE") == b"\x00\x01"


@pytest.mark.parametrize(
    "hex_bytes",
    [
        "",  # no ISCC-UNIT
        "28000100000000",  # nonzero header padding nibble
        "200800" + "00" * 36,  # Length field 8
        "3000000000",  # truncated ISCC-BODY
        "30000000000000",  # trailing byte after a complete ISCC-UNIT
        "5005578c7c8fbfbdb58fa88248c53a852c8d05b41f3472493225a522af4a2c9cbf6a",  # ISCC-CODE
        "60100000000000000000",  # ISCC-ID
    ],
)
def test_decode_value_rejects_iep_0020_reject_vectors(hex_bytes):
    with pytest.raises(ValueError):
        decode_value(base64.b64encode(bytes.fromhex(hex_bytes)).decode())


def test_decode_value_drops_short_units():
    # IEP-0020 Vector 2: 64- and 128-bit Content-Codes are ignored, the 256-bit Data-Code is kept
    vector_2 = (
        "21018c3585376ee22d5a22039f07f3ff9e0061ff3ef7cbffffef37ff300705b41f3472493225d27edcbdb63236846a53ee28"
        "deca5e3561acc0c9bbdf81b5"
    )
    assert decode_value(base64.b64encode(bytes.fromhex(vector_2)).decode()) == [DATA_CODE]


def test_decode_value_ignores_unknown_subtype_next_to_valid_units():
    unknown = bytes.fromhex("270000000000")  # CONTENT SubType 7, accepted by IEP-0020 decoders
    value = base64.b64encode(unknown + ic.encode_seq([DATA_CODE])).decode()
    assert decode_value(value) == [DATA_CODE]


def test_decode_value_rejects_meta_code_only():
    with pytest.raises(ValueError, match="256-bit"):
        decode_value(encode([META]))


def test_decode_value_rejects_unknown_subtype_unit():
    # IEP-0020: decoders accept CONTENT SubType 7, but a 32-bit unit is not searchable
    with pytest.raises(ValueError, match="256-bit"):
        decode_value(base64.b64encode(bytes.fromhex("270000000000")).decode())


def test_decode_value_rejects_invalid_base64():
    with pytest.raises(ValueError, match="base64"):
        decode_value("not*base64")


def test_decode_value_rejects_overlong_value():
    with pytest.raises(ValueError, match="exceeds"):
        decode_value("A" * (MAX_VALUE_LENGTH + 1))


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        (CONTENT, True),
        (DATA_CODE, True),
        (INSTANCE, True),
        (META, False),
        ("ISCC:EAASKDNZNYGUUF5A", False),  # 64-bit Content-Code
    ],
)
def test_is_searchable(unit, expected):
    assert is_searchable(unit) is expected
