import pytest
from reach.safety import check_test_number, mask
from reach.script import build_script, build_twiml
from reach.location import Location, manual_location
from reach.caller import TwilioCaller, make_caller, PreviewCaller

@pytest.mark.parametrize("n", ["911", "+911", "112", "+112", "999", "000", "+1 911", "12345", "3145550123"])
def test_refuses_emergency_and_short_numbers(n):
    with pytest.raises(ValueError):
        check_test_number(n)

def test_accepts_ordinary_number():
    assert check_test_number("+1 (314) 555-0123") == "+13145550123"
    assert mask("+13145550123") == "•••0123"

def test_script_only_contains_selected_facts():
    loc = Location("manual", "Demo Hall", 38.6, -90.2, 30)
    s = build_script(kind="emergency", name="Ana", facts={"medical"}, location=loc, share_location=True)
    assert "Ana" in s and "medical help" in s and "Demo Hall" in s and "38.6000 north" in s
    assert "cannot speak" not in s and "unsafe" not in s

def test_location_not_shared_or_missing():
    s = build_script(kind="contact", name="Ana", facts=set(), location=None, share_location=False, recipient_label="Sam")
    assert "Location was not shared" in s and s.startswith("Hello Sam.")
    s = build_script(kind="emergency", name="Ana", facts=set(), location=None, share_location=True)
    assert "Location unavailable" in s

def test_ip_location_is_labelled_approximate():
    assert "Approximate" in Location("ip", "St. Louis").spoken()

def test_manual_location_rejects_bad_coordinates():
    assert manual_location({"location": {"lat": 200, "lon": 0}}) is None

def test_twiml_escapes_and_repeats():
    x = build_twiml("A & <b>", repeats=2)
    assert "A &amp; &lt;b&gt;" in x and x.count("A &amp;") == 2 and x.startswith("<Response>")

def test_default_mode_never_places_real_calls():
    assert isinstance(make_caller({}), PreviewCaller)
    with pytest.raises(ValueError):
        make_caller({"call_mode": "twilio", "twilio": {}})
