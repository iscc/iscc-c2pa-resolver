"""Decoding of `io.iscc.v0` soft binding values (IEP-0020) into searchable ISCC-UNITs."""

import base64
import binascii

import iscc_core as ic

ALG = "io.iscc.v0"
MAX_VALUE_LENGTH = 4096  # base64 characters, about 90 ISCC-UNITs of 256 bits
SEARCHABLE_TYPES = {ic.MT.SEMANTIC, ic.MT.CONTENT, ic.MT.DATA, ic.MT.INSTANCE}


def decode_base64(value):
    # type: (str) -> bytes
    """Decode base64 leniently: standard or URL-safe alphabet, with or without padding.

    A `+` that arrives as a space because the client did not percent-encode the query is restored.
    """
    text = value.strip().replace(" ", "+").replace("-", "+").replace("_", "/").rstrip("=")
    text += "=" * (-len(text) % 4)
    return base64.b64decode(text, validate=True)


def is_searchable(unit):
    # type: (str) -> bool
    """Tell whether an ISCC-UNIT is usable for matching: Version 0, 256-bit body, no Meta-Code.

    IEP-0020 requires resolvers to ignore shorter units. Meta-Codes are left out because equal metadata says
    nothing about equal content, so they never contribute to a match. Units with a SubType unknown to iscc-core
    are ignored, as IEP-0020 requires, instead of failing the query.
    """
    try:
        maintype, _subtype, version, _length, body = ic.iscc_decode(unit)
    except ValueError:
        return False
    return maintype in SEARCHABLE_TYPES and version == ic.VS.V0 and len(body) == 32


def decode_value(value):
    # type: (str) -> list[str]
    """Turn a base64 `io.iscc.v0` value into its searchable ISCC-UNITs, deduplicated, in input order.

    :raises ValueError: if the value is too long, not base64, not a valid ISCC-SEQ, or has no searchable unit.
    """
    if len(value) > MAX_VALUE_LENGTH:
        raise ValueError(f"Value exceeds {MAX_VALUE_LENGTH} characters")
    try:
        seq = decode_base64(value)
    except binascii.Error as e:
        raise ValueError("Value is not valid base64") from e
    units = ic.decode_seq(seq)
    searchable = [unit for unit in dict.fromkeys(units) if is_searchable(unit)]
    if not searchable:
        raise ValueError("Value holds no 256-bit Version 0 Semantic-, Content-, Data- or Instance-Code")
    return searchable
