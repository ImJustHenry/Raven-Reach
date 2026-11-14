"""Number guards. The 'emergency' button in this hackathon build only ever dials a
configured TEST phone that you own. Real emergency/short codes are refused."""
import re

E164 = re.compile(r"^\+[1-9][0-9]{7,14}$")

# Common emergency / service short codes worldwide. E.164 length already rejects
# these, but we also refuse them if someone pastes them with a '+' prefix.
EMERGENCY_CODES = {
    "911", "112", "999", "000", "110", "119", "118", "100", "101", "102", "108",
    "120", "122", "190", "192", "193", "113", "115", "123", "15", "17", "18",
    "08", "061", "062", "066", "10111", "1122", "911911",
}


def normalize(number: str) -> str:
    """Strip spaces, dashes, dots and parentheses; keep a leading '+'."""
    raw = (number or "").strip()
    plus = raw.startswith("+")
    digits = re.sub(r"[^0-9]", "", raw)
    return ("+" if plus else "") + digits


def is_emergency_code(number: str) -> bool:
    digits = normalize(number).lstrip("+")
    return digits in EMERGENCY_CODES


def check_test_number(number: str) -> str:
    """Return the normalized E.164 number, or raise ValueError.

    Only full-length ordinary numbers (that you control / have consent to call)
    are accepted. Short codes and known emergency numbers are always refused.
    """
    value = normalize(number)
    if not value:
        raise ValueError("No phone number configured")
    if is_emergency_code(value):
        raise ValueError("Refusing to dial a real emergency number. Use your own test phone.")
    if not E164.match(value):
        raise ValueError("Use full international format, e.g. +13145550123")
    return value


def mask(number: str) -> str:
    value = normalize(number)
    return ("•••" + value[-4:]) if len(value) >= 4 else "••••"
