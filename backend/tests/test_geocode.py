"""Geocoding: provider parsing (fake HTTP), chain/cache, and address resolution in the UnderstandingAgent."""
from types import SimpleNamespace

import pytest

from app.agents import understanding
from app.providers.geocode import factory
from app.providers.geocode.base import GeocodeError, GeocodeResult
from app.providers.geocode.nominatim import NominatimGeocoder
from app.providers.geocode.onemap import OneMapGeocoder
from app.services import location_service


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def _resp(self, payload, status=200):
        return SimpleNamespace(status_code=status, json=lambda: payload, raise_for_status=lambda: None)

    def get(self, url, params=None, headers=None):
        self.calls.append(("GET", url, params, headers))
        return self._resp(self.responses.pop(0))

    def post(self, url, json=None):
        self.calls.append(("POST", url, json, None))
        return self._resp(self.responses.pop(0))


def test_onemap_fetches_token_and_parses_results():
    token = {"access_token": "tok123", "expiry_timestamp": 9999999999}
    search = {"found": 1, "results": [{"SEARCHVAL": "123 TAMPINES STREET 11", "BLK_NO": "123", "ROAD_NAME": "TAMPINES STREET 11",
                                      "BUILDING": "NIL", "ADDRESS": "123 TAMPINES STREET 11 SINGAPORE 521123", "POSTAL": "521123",
                                      "LATITUDE": "1.3461", "LONGITUDE": "103.9452"}]}
    g = OneMapGeocoder(email="a@b.c", password="pw", client=FakeHttp([token, search]))
    out = g.search("Blk 123 Tampines St 11")
    assert out[0].postal == "521123" and out[0].lat == 1.3461 and out[0].source == "onemap"
    assert g._client.calls[0][0] == "POST" and g._client.calls[1][3]["Authorization"] == "Bearer tok123"
    with pytest.raises(GeocodeError):
        OneMapGeocoder()  # no credentials


def test_onemap_error_payload_raises():
    g = OneMapGeocoder(token="static", client=FakeHttp([{"error": "Authentication token missing", "results": []}]))
    with pytest.raises(GeocodeError):
        g.search("x")


def test_nominatim_parses_and_filters_country():
    payload = [{"lat": "1.3725735", "lon": "103.8937879", "display_name": "Hougang Mall, 90, Hougang Avenue 10, Hougang Central, Singapore",
                "name": "Hougang Mall", "type": "mall", "importance": 0.31, "address": {"postcode": "538766"}}]
    g = NominatimGeocoder(client=FakeHttp([payload]))
    out = g.search("Hougang Mall")
    assert out[0].name == "Hougang Mall" and out[0].postal == "538766" and out[0].kind == "mall"
    assert g._client.calls[0][2]["countrycodes"] == "sg"


def test_resolution_exact_ambiguous_and_fallback(db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(location_service, "supports_arbitrary_points", lambda: True)
    hits = [GeocodeResult("Blk 123", "123 Tampines Street 11 Singapore 521123", 1.3461, 103.9452, "onemap", "521123"),
            GeocodeResult("Blk 125", "125 Tampines Street 11 Singapore 521125", 1.3464, 103.9499, "onemap", "521125")]
    monkeypatch.setattr(understanding, "resolve_location_ex", understanding.resolve_location_ex)  # no-op guard
    monkeypatch.setattr(factory, "enabled", lambda: True)
    monkeypatch.setattr(factory, "geocode", lambda q, limit=5: SimpleNamespace(results=hits, provider="onemap", degraded=False, reason=None))
    # block number typed → the hit carrying that number wins → exact point
    r = understanding.resolve_location_ex(db_session, "Blk 123 Tampines St 11")
    assert r.location and r.location["id"].startswith("pt_") and r.provider == "onemap" and not r.candidates
    # street only → both hits plausible → candidates for the customer
    r2 = understanding.resolve_location_ex(db_session, "Tampines Street 11")
    assert r2.location is None and len(r2.candidates) == 2
    # unit numbers are not address numbers; a postal code is queried on its own; a single hit is accepted
    seen_queries: list[str] = []
    monkeypatch.setattr(factory, "geocode", lambda q, limit=5: (seen_queries.append(q), SimpleNamespace(results=hits[:1], provider="onemap", degraded=False, reason=None))[1])
    r5 = understanding.resolve_location_ex(db_session, "Blk 123 Tampines St 11 #05-123")
    assert r5.location and r5.location["name"].startswith("123 Tampines")
    r6 = understanding.resolve_location_ex(db_session, "邮编 521123")
    assert seen_queries[-1] == "521123" and r6.location
    # plain area name never hits the geocoder
    r3 = understanding.resolve_location_ex(db_session, "Tampines")
    assert r3.location and r3.location["id"] == "loc_tampines" and r3.provider == "preset"
    # geocoder down → area substring fallback with a note
    monkeypatch.setattr(factory, "geocode", lambda q, limit=5: SimpleNamespace(results=[], provider=None, degraded=True, reason="onemap: 401; nominatim: timeout"))
    r4 = understanding.resolve_location_ex(db_session, "Blk 9 Bedok North")
    assert r4.location and r4.location["id"] == "loc_bedok" and "unavailable" in (r4.note or "")


def test_mock_extracts_address_hints():
    from app.providers.llm.mock import MockLLMProvider
    m = MockLLMProvider()
    assert m.interpret("aircon leaking at Blk 123 Tampines St 11", [], {"location_names": []}).location_hint.startswith("blk 123")
    assert m.interpret("my fridge is broken, 521123", [], {"location_names": []}).location_hint == "521123"
    assert m.interpret("toilet clogged, 45 Jalan Besar Road", [], {"location_names": []}).location_hint == "45 jalan besar road"


def test_address_normalization():
    n = OneMapGeocoder.normalize
    assert n("Blk 125 Tampines St 11") == "125 Tampines St 11"
    assert n("Block 90, Hougang Ave 10, #05-123, Singapore") == "90 Hougang Ave 10"
    assert n("blk9 bedok north street 1") == "9 bedok north street 1"
    assert n("B210A clementi ave 6") == "210A clementi ave 6"      # 'B' glued to the block number
    assert n("Bedok Mall") == "Bedok Mall"                        # a leading B in a word is untouched


def test_chain_continues_when_first_provider_finds_nothing(monkeypatch):
    class Empty:
        name = "onemap"
        def search(self, q, limit=5): return []
    class Hit:
        name = "nominatim"
        def search(self, q, limit=5): return [GeocodeResult("x", "X Street", 1.3, 103.8, "nominatim")]
    monkeypatch.setattr(factory, "providers", lambda: [Empty(), Hit()])
    factory._memory.clear()
    monkeypatch.setattr(factory, "_load_disk", dict)
    monkeypatch.setattr(factory, "_cache_file", lambda: __import__("pathlib").Path("/dev/null"))
    out = factory.geocode("some street nobody knows")
    assert out.provider == "nominatim" and out.results and not out.degraded
    monkeypatch.setattr(factory, "providers", lambda: [Empty()])
    out2 = factory.geocode("another unknown street")
    assert out2.provider == "onemap" and not out2.results and "no match" in (out2.reason or "")
