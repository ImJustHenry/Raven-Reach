import json, time
import pytest, requests
from reach.phone_bridge import PhoneBridge, parse_fix
from reach.location import resolve
from reach.script import build_script

def test_parse_fix_validates():
    loc = parse_fix(json.dumps({"token": "abc123", "lat": 38.6, "lon": -90.2, "accuracy": 8, "address": "Main St"}), "abc123")
    assert loc.source == "phone" and loc.accuracy_m == 8 and loc.address == "Main St"
    with pytest.raises(PermissionError):
        parse_fix(json.dumps({"token": "nope", "lat": 1, "lon": 1}), "abc123")
    with pytest.raises(ValueError):
        parse_fix(json.dumps({"token": "abc123", "lat": 100, "lon": 1}), "abc123")

def test_http_roundtrip_and_resolve_prefers_fresh_phone_fix():
    b = PhoneBridge("abc123", port=0, host="127.0.0.1")
    assert b.start()
    port = b.server.server_address[1]
    try:
        url = f"http://127.0.0.1:{port}/location"
        assert requests.post(url, json={"token": "bad", "lat": 1, "lon": 1}, timeout=3).status_code == 403
        assert requests.post(url, json={"token": "abc123", "lat": 38.6, "lon": -90.2, "accuracy": 12}, timeout=3).json()["ok"]
        loc = resolve({"location": {"address": "Typed address"}}, b)
        assert loc.source == "phone" and loc.lat == 38.6
        s = build_script(kind="emergency", name="Ana", facts=set(), location=loc, share_location=True)
        assert "Phone GPS location" in s and "12 meters" in s
        b.fix = type(loc)("phone", "", 1, 1, None, time.time() - 600)  # stale
        assert resolve({"location": {"address": "Typed address"}}, b).source == "manual"
    finally:
        b.close()

def test_short_token_rejected():
    with pytest.raises(ValueError):
        PhoneBridge("abc")


def test_request_handed_to_phone_once_and_status_flow():
    from reach.caller import PhoneAppCaller
    b = PhoneBridge("abc123", port=0, host="127.0.0.1"); assert b.start()
    base = f"http://127.0.0.1:{b.server.server_address[1]}"
    try:
        caller = PhoneAppCaller(b)
        rid = caller.place("+13145550123", "Hello script")
        assert caller.status(rid) == "waiting_phone"
        assert requests.get(base + "/request", headers={"X-Reach-Token": "bad"}, timeout=3).status_code == 403
        req = requests.get(base + "/request", headers={"X-Reach-Token": "abc123"}, timeout=3).json()["request"]
        assert req["id"] == rid and req["script"] == "Hello script" and req["number"] == "+13145550123"
        post = lambda st: requests.post(base + "/request/status", json={"token": "abc123", "id": rid, "status": st}, timeout=3)
        assert post("phone_received").json()["ok"]
        # delivered once: a second poll never re-offers the same call
        assert requests.get(base + "/request", headers={"X-Reach-Token": "abc123"}, timeout=3).json()["request"] is None
        assert post("speaking").ok and caller.status(rid) == "speaking"
        assert post("hacked").status_code == 400
        assert requests.post(base + "/request/status", json={"token": "abc123", "id": "nope", "status": "spoken"}, timeout=3).status_code == 404
        assert b.phone_online()
        with pytest.raises(RuntimeError):
            caller.hangup(rid)
    finally:
        b.close()


def test_unclaimed_request_expires(monkeypatch):
    import reach.phone_bridge as pb
    b = PhoneBridge("abc123")
    b.offer("r1", "+13145550123", "", "x")
    monkeypatch.setattr(pb, "REQUEST_TTL", -1)
    assert b.take_request() is None and b.request_status("r1") == "expired"


def test_contacts_sync_validates_sorts_and_needs_token():
    b = PhoneBridge("abc123", port=0, host="127.0.0.1"); assert b.start()
    url = f"http://127.0.0.1:{b.server.server_address[1]}/contacts"
    try:
        body = {"contacts": [
            {"name": "Zed", "number": "+13145550101"},
            {"name": "Mom", "number": "+1 (314) 555-0102", "starred": True},
            {"name": "Bad", "number": "911"},
            {"name": "", "number": "+13145550103"},
            {"name": "Local", "number": "5550104"},
            {"name": "Zed", "number": "+13145550101"}]}
        assert requests.post(url, json=body, timeout=3).status_code == 403
        r = requests.post(url, json=body, headers={"X-Reach-Token": "abc123"}, timeout=3).json()
        assert r == {"ok": True, "accepted": 2}
        assert [c["name"] for c in b.phone_contacts()] == ["Mom", "Zed"]   # favourites first
        assert b.phone_contacts()[0]["number"] == "+13145550102"
    finally:
        b.close()


def test_sos_number_from_phone_refuses_emergency_numbers():
    b = PhoneBridge("abc123", port=0, host="127.0.0.1"); assert b.start()
    url = f"http://127.0.0.1:{b.server.server_address[1]}/settings"
    h = {"X-Reach-Token": "abc123"}
    try:
        assert requests.post(url, json={"sos_number": "+13145550199"}, timeout=3).status_code == 403
        for bad in ("911", "+911", "112", "999", "5550199"):
            assert requests.post(url, json={"sos_number": bad}, headers=h, timeout=3).status_code == 400
        assert b.phone_sos_number() == ""
        assert requests.post(url, json={"sos_number": "+1 314 555 0199"}, headers=h, timeout=3).json()["ok"]
        assert b.phone_sos_number() == "+13145550199"
    finally:
        b.close()


def test_pair_url_has_host_and_token():
    from reach.phone_bridge import pair_url
    from urllib.parse import urlparse, parse_qs
    b = PhoneBridge("abc123", port=0, host="127.0.0.1"); assert b.start()
    try:
        u = urlparse(pair_url(b)); q = parse_qs(u.query)
        assert u.scheme == "ravenreach" and u.netloc == "pair"
        assert q["t"] == ["abc123"] and q["h"][0].endswith(f":{b.server.server_address[1]}")
    finally:
        b.close()


def test_profile_from_phone_is_used_in_script():
    b = PhoneBridge("abc123", port=0, host="127.0.0.1"); assert b.start()
    url = f"http://127.0.0.1:{b.server.server_address[1]}/settings"
    try:
        r = requests.post(url, json={"wearer_name": "Henry W.\x07", "description": "Red jacket, glasses. " * 30},
                          headers={"X-Reach-Token": "abc123"}, timeout=3)
        assert r.json()["ok"]
        p = b.phone_profile()
        assert p["name"] == "Henry W." and len(p["description"]) == 300
        s = build_script(kind="emergency", name=p["name"], facts=set(), location=None, share_location=False,
                         description=p["description"])
        assert "on behalf of Henry W." in s and "Red jacket" in s
    finally:
        b.close()
