"""OSRM adapter (mocked HTTP), degradation, and map-point resolution — no network."""
from types import SimpleNamespace

import pytest

from app.providers.route.base import LocationPoint
from app.providers.route.osrm import OSRMRouteProvider, RouteProviderError
from app.services import location_service
from app.services.order_service import OrderError

A, B, C = LocationPoint("a", "A", 1.30, 103.80), LocationPoint("b", "B", 1.35, 103.90), LocationPoint("c", "C", 1.40, 103.70)


class FakeClient:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        p = self.payloads.pop(0)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: p)


def test_table_applies_factor_and_base_and_keeps_unreachable_none():
    table = {"code": "Ok", "durations": [[0, 588, None], [540, 0, 900], [None, 880, 0]],
             "distances": [[0, 7400, None], [7200, 0, 12000], [None, 11800, 0]]}
    p = OSRMRouteProvider("http://osrm.test", client=FakeClient([table]), duration_factor=1.25, base_minutes=3)
    m = p.travel_matrix([B, A, C])  # unsorted input; adapter sorts by id
    assert "103.800000,1.300000;103.900000,1.350000;103.700000,1.400000" in p._client.urls[0]  # lon,lat order, sorted ids
    assert m.travel("a", "b") == 13 + 3   # ceil(588/60*1.25)=13, +3
    assert m.travel("b", "a") == 12 + 3   # asymmetric
    assert m.travel("a", "c") is None and m.travel("c", "a") is None  # never 0
    assert m.distance_km[("a", "b")] == 7.4 and m.travel("a", "a") == 0
    assert m.provider == "osrm" and m.snapshot_id.startswith("osrm:")


def test_bad_code_and_malformed_raise():
    p = OSRMRouteProvider("http://osrm.test", client=FakeClient([{"code": "NoTable", "message": "x"}]), retries=0)
    with pytest.raises(RouteProviderError):
        p.travel_matrix([A, B])
    p2 = OSRMRouteProvider("http://osrm.test", client=FakeClient([{"code": "Ok", "durations": [[0]]}]), retries=0)
    with pytest.raises(RouteProviderError):
        p2.travel_matrix([A, B])


def test_geometry_parsed_and_cached():
    route = {"code": "Ok", "routes": [{"duration": 588, "distance": 7400, "geometry": {"coordinates": [[103.8, 1.3], [103.85, 1.32], [103.9, 1.35]]}}]}
    p = OSRMRouteProvider("http://osrm.test", client=FakeClient([route]))
    g = p.geometry(A, B)
    assert g.points[0] == (1.3, 103.8) and not g.schematic and g.minutes == 9.8 and g.distance_km == 7.4
    assert p.geometry(A, B) is g and len(p._client.urls) == 1  # cached


def test_resolve_point_snaps_in_fixture_mode_and_rejects_outside(db_session):
    out = location_service.resolve_point(db_session, 1.3526, 103.9440)  # next to Tampines Hub
    assert out["snapped"] and out["location"]["id"] == "loc_tampines"
    with pytest.raises(OrderError) as exc:
        location_service.resolve_point(db_session, 3.14, 101.69)  # Kuala Lumpur
    assert exc.value.code == "outside_service_area"


def test_resolve_point_exact_when_provider_supports_it(db_session, monkeypatch):
    monkeypatch.setattr(location_service, "supports_arbitrary_points", lambda: True)
    out = location_service.resolve_point(db_session, 1.35913, 104.0062, "Blk 1 Changi")
    assert not out["snapped"] and out["location"]["id"].startswith("pt_") and out["location"]["name"] == "Blk 1 Changi"
    again = location_service.resolve_point(db_session, 1.35913, 104.0062)
    assert again["location"]["id"] == out["location"]["id"]  # stable id for the same point
