import struct
import pytest
from reach import phone_calls as pc
from reach.bluez_accessory import advertisement_properties, find_iphone, ANCS_SERVICE

def src(event, flags, cat, uid):
    return struct.pack("<BBBBI", event, flags, cat, 1, uid)

def test_parse_notification_source():
    ev = pc.parse_notification_source(src(0, pc.FLAG_POSITIVE | pc.FLAG_NEGATIVE, 1, 0x01020304))
    assert ev == {"event": 0, "flags": 24, "category": 1, "count": 1, "uid": 0x01020304}
    with pytest.raises(ValueError):
        pc.parse_notification_source(b"\x00" * 7)

def test_commands_bytes():
    assert pc.get_attributes_command(5) == bytes([0, 5, 0, 0, 0, 0, 1, 64, 0])
    assert pc.action_command(5, True) == bytes([2, 5, 0, 0, 0, 0])
    assert pc.action_command(5, False) == bytes([2, 5, 0, 0, 0, 1])

def test_fragmented_attribute_reply():
    app, title = b"com.apple.mobilephone", "Mom ❤".encode()
    raw = bytes([0]) + struct.pack("<I", 7) + bytes([0]) + struct.pack("<H", len(app)) + app \
        + bytes([1]) + struct.pack("<H", len(title)) + title
    r = pc.AttributeResponse(7)
    assert r.feed(raw[:10]) is None
    assert r.feed(raw[10:]) == {0: "com.apple.mobilephone", 1: "Mom ❤"}
    with pytest.raises(ValueError):
        pc.AttributeResponse(8).feed(raw)

def test_callbook_lifecycle():
    book = pc.CallBook(clock=lambda: 1.0)
    assert book.on_source(pc.parse_notification_source(src(0, 24, 1, 9))) == 9
    book.on_attributes(9, {1: "+1 314 555 0123"})
    c = book.current(); assert c.state == "ringing" and c.caller == "+1 314 555 0123" and c.can_answer
    book.calls[9] = pc.replace(c, state="answering")
    book.on_source(pc.parse_notification_source(src(2, 0, 1, 9)))
    assert book.current().state == "answered"

def test_removed_without_action_is_ended_not_answered():
    book = pc.CallBook()
    book.on_source(pc.parse_notification_source(src(0, 24, 1, 3)))
    book.on_source(pc.parse_notification_source(src(2, 0, 1, 3)))
    assert book.current().state == "ended"

def test_other_categories_ignored_and_preexisting_missed_ignored():
    book = pc.CallBook()
    assert book.on_source(pc.parse_notification_source(src(0, 0, 4, 1))) is None  # social/message
    assert book.on_source(pc.parse_notification_source(src(0, pc.FLAG_PRE_EXISTING, 2, 2))) is None
    assert book.current() is None and book.missed == []

def test_simulated_phone_answer_and_decline():
    p = pc.SimulatedPhone(); p.ring("Sam")
    uid = p.snapshot()["call"].uid; p.answer(uid)
    assert p.snapshot()["call"].state == "answered"
    p.ring("Lee"); uid2 = p.snapshot()["call"].uid; p.decline(uid2)
    assert p.snapshot()["call"].state == "declined"

def test_real_link_refused_on_mac(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    with pytest.raises(ValueError):
        pc.IphoneCallLink()

def test_advertisement_solicits_ancs():
    assert advertisement_properties("Raven Reach")["SolicitUUIDs"] == [ANCS_SERVICE]

def test_find_iphone_requires_ancs_and_bond():
    a = "/org/bluez/hci0"; d = a + "/dev_AA"
    tree = {d: {"org.bluez.Device1": {"Address": "AA", "Connected": True, "ServicesResolved": True, "Paired": True}},
            d + "/service1": {"org.bluez.GattService1": {"UUID": ANCS_SERVICE.upper()}}}
    assert find_iphone(tree, a)[0] == d
    tree[d]["org.bluez.Device1"]["Paired"] = False
    assert find_iphone(tree, a) is None
