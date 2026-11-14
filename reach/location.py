"""Where the wearer is, as honestly as we can say it.

Sources, in order:
  0. 'phone'   – live GPS sent by the paired phone over Wi-Fi (reach/phone_bridge.py),
                 used only while fresh (under 2 minutes old).
  1. 'manual'  – an address / coordinates you typed into the config.
  2. 'ip'      – approximate city-level fix from your internet address
                 (only if you set "ip_location_consent": true; it contacts ipapi.co).
Nothing is invented: if no source is available the call says location is unavailable.
"""
from dataclasses import dataclass
import time


@dataclass(frozen=True)
class Location:
    source: str                 # 'phone' | 'manual' | 'ip'
    address: str = ""           # human readable, only if provided by the source
    lat: float | None = None
    lon: float | None = None
    accuracy_m: float | None = None
    fetched_at: float = 0.0

    def spoken(self) -> str:
        parts = []
        if self.address:
            parts.append(self.address)
        if self.lat is not None and self.lon is not None:
            ns = "north" if self.lat >= 0 else "south"
            ew = "east" if self.lon >= 0 else "west"
            parts.append(f"GPS coordinates {abs(self.lat):.4f} {ns}, {abs(self.lon):.4f} {ew}")
        if not parts:
            return "Location unavailable."
        where = ". ".join(parts)
        if self.source == "phone":
            age = max(0, int(time.time() - self.fetched_at))
            acc = f", accurate to about {self.accuracy_m:.0f} meters" if self.accuracy_m else ""
            return f"Phone GPS location, {age} seconds old{acc}: {where}."
        if self.source == "ip":
            return f"Approximate location, estimated from internet address, may be off by several kilometers: {where}."
        acc = f", accuracy about {self.accuracy_m:.0f} meters" if self.accuracy_m else ""
        return f"Location as set by the wearer{acc}: {where}."

    def short(self) -> str:
        label = self.address or (f"{self.lat:.4f}, {self.lon:.4f}" if self.lat is not None else "unknown")
        kind = {"ip": "approx. from IP", "phone": f"phone GPS, {int(time.time() - self.fetched_at)}s old"}
        return f"{label} ({kind.get(self.source, 'manual')})"

    def map_link(self) -> str:
        if self.lat is None or self.lon is None:
            return ""
        return f"https://maps.google.com/?q={self.lat:.6f},{self.lon:.6f}"


def manual_location(config) -> Location | None:
    data = config.get("location") or {}
    address = str(data.get("address", "")).strip()
    lat, lon = data.get("lat"), data.get("lon")
    try:
        lat = None if lat in (None, "") else float(lat)
        lon = None if lon in (None, "") else float(lon)
    except (TypeError, ValueError):
        lat = lon = None
    if lat is not None and not (-90 <= lat <= 90 and lon is not None and -180 <= lon <= 180):
        lat = lon = None
    if not address and lat is None:
        return None
    acc = data.get("accuracy_m")
    return Location("manual", address, lat, lon, float(acc) if acc else None, time.time())


def ip_location(timeout=4.0) -> Location | None:
    import requests
    try:
        r = requests.get("https://ipapi.co/json/", timeout=timeout,
                         headers={"User-Agent": "raven-reach-hackathon"})
        r.raise_for_status()
        d = r.json()
        city = ", ".join(x for x in (d.get("city"), d.get("region"), d.get("country_name")) if x)
        lat, lon = d.get("latitude"), d.get("longitude")
        return Location("ip", city, float(lat) if lat is not None else None,
                        float(lon) if lon is not None else None, None, time.time())
    except Exception:
        return None


def resolve(config, bridge=None) -> Location | None:
    if bridge is not None:
        fix = bridge.fresh()
        if fix is not None:
            return fix
    loc = manual_location(config)
    if loc is not None:
        return loc
    if config.get("ip_location_consent") is True:
        return ip_location()
    return None
