"""Property-based tests (Hypothesis) for the code that handles untrusted input: query values and gateway URLs."""

import base64
import ipaddress

import iscc_core as ic
from hypothesis import given
from hypothesis import strategies as st

from iscc_c2pa_resolver.binding import decode_value, is_searchable
from iscc_c2pa_resolver.gateway import is_https_url, is_manifest_id, parse_pointer
from iscc_c2pa_resolver.netguard import is_public
from iscc_c2pa_resolver.resolve import similarity_score

UNIT_TYPES = [
    (ic.MT.SEMANTIC, ic.ST_CC.TEXT),
    (ic.MT.CONTENT, ic.ST_CC.IMAGE),
    (ic.MT.CONTENT, ic.ST_CC.AUDIO),
    (ic.MT.DATA, ic.ST.NONE),
    (ic.MT.INSTANCE, ic.ST.NONE),
]

searchable_units = st.builds(
    lambda kind, body: "ISCC:" + ic.encode_component(kind[0], kind[1], ic.VS.V0, 256, body),
    st.sampled_from(UNIT_TYPES),
    st.binary(min_size=32, max_size=32),
)

manifest_address_like = st.builds(
    lambda scheme, base, manifest_id: f"{scheme}://repo.example/{base}/manifests/{manifest_id}",
    st.sampled_from(["https", "http"]),
    st.text(max_size=20),
    st.text(max_size=60),
)


@given(st.text(max_size=300))
def test_decode_value_only_raises_value_error_on_text(value):
    try:
        units = decode_value(value)
    except ValueError:
        return
    assert units
    assert all(is_searchable(unit) for unit in units)


@given(st.binary(max_size=300))
def test_decode_value_only_raises_value_error_on_bytes(data):
    try:
        units = decode_value(base64.b64encode(data).decode())
    except ValueError:
        return
    assert all(is_searchable(unit) for unit in units)


@given(st.lists(searchable_units, min_size=1, max_size=8), st.sampled_from(["standard", "unpadded", "urlsafe"]))
def test_decode_value_round_trips_any_encoding(units, variant):
    value = base64.b64encode(ic.encode_seq(units)).decode()
    if variant == "unpadded":
        value = value.rstrip("=")
    elif variant == "urlsafe":
        value = value.replace("+", "-").replace("/", "_")
    assert decode_value(value) == list(dict.fromkeys(units))


@given(st.text(max_size=200) | manifest_address_like)
def test_parse_pointer_never_crashes_and_returns_https(url):
    pointer = parse_pointer(url)
    assert pointer is None or (
        pointer.manifest_url == url and is_https_url(url) and is_manifest_id(pointer.manifest_id)
    )


@given(st.text(max_size=200))
def test_is_https_url_never_crashes(value):
    assert is_https_url(value) in (True, False)


@given(
    st.dictionaries(
        st.sampled_from(["META_NONE_V0", "SEMANTIC_TEXT_V0", "CONTENT_IMAGE_V0", "DATA_NONE_V0", "INSTANCE_NONE_V0"]),
        st.floats(min_value=0.0, max_value=1.0),
    )
)
def test_similarity_score_bounds(types):
    score = similarity_score(types)
    exact = types.get("INSTANCE_NONE_V0", 0.0) >= 1.0
    if exact:
        assert score == 100
    elif score is not None:
        assert 0 <= score <= 99


@given(st.ip_addresses())
def test_is_public_agrees_with_ipaddress(address):
    ip = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped else address
    assert is_public(str(address)) == (ip.is_global and not ip.is_multicast)
