"""Property-based API tests: Schemathesis generates requests from openapi.yaml and checks every response against it.

This catches drift between the hand-written spec and the code: undocumented status codes, responses that violate
their schema (including the verbatim C2PA schemas), server errors, and valid requests the app rejects.
"""

from pathlib import Path

import schemathesis
from hypothesis import HealthCheck, settings
from schemathesis.specs.openapi.checks import positive_data_acceptance

import iscc_c2pa_resolver
from iscc_c2pa_resolver.app import create_app
from iscc_c2pa_resolver.settings import Settings
from tests.conftest import ID_EARLY, MANIFEST_URL, SEARCH_URL, FakeNetwork, hit

SPEC = Path(iscc_c2pa_resolver.__file__).parent / "openapi" / "openapi.yaml"


def fake_network():
    # type: () -> FakeNetwork
    """A network where every search finds one declaration whose gateway URL serves a manifest."""
    network = FakeNetwork()
    network.json(SEARCH_URL + "/readyz", {"status": "ready"})
    network.hits(hit(ID_EARLY, MANIFEST_URL, CONTENT_TEXT_V0=0.93))
    network.manifest()
    return network


app = create_app(Settings(search_url=SEARCH_URL), fake_network().transport)
# Loaded from disk so that the relative reference to c2pa-sbr.json resolves; requests go to the app in-process.
schema = schemathesis.openapi.from_path(SPEC)
schema.app = app


@schema.parametrize()
@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_api_conforms_to_spec(case):
    # A schema-valid string is not necessarily a valid ISCC-SEQ, which JSON Schema cannot express. The resolver
    # rightly answers such values with 400, so the check that all schema-valid requests succeed does not apply.
    case.call_and_validate(excluded_checks=[positive_data_acceptance])
