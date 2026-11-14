"""Phone GPS over Wi-Fi.

The Reach app (on the glasses, or the Mac simulator) listens on the local network.
The phone POSTs its own GPS fix, e.g. from an iPhone Shortcut:

    POST http://<glasses-ip>:8765/location
    {"token": "<pairing token>", "lat": 38.63, "lon": -90.20,
     "accuracy": 12, "address": "Street, City"}      # accuracy/address optional

The iPhone app (ios/RavenReachPhone) also polls for a confirmed emergency request:

    GET  /request          header X-Reach-Token -> {"request": {...} | null}
    POST /request/status   {"token", "id", "status"}   status reported by the phone

A request is handed to the phone ONCE (it disappears when the phone reports
"phone_received") and expires after 60 s, so a late or repeated poll can never
start a second call.

The token stops other people on the same Wi-Fi from injecting a fake location.
Only the newest fix is kept, in memory. Nothing is written to disk or uploaded.
"""
import json
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .location import Location

STALE_SECONDS = 120
MAX_BODY = 4096
MAX_CONTACTS_BODY = 512 * 1024
MAX_CONTACTS = 1000
REQUEST_TTL = 60
PHONE_STATUSES = {"phone_received", "call_prompt", "call_connected", "speaking", "spoken",
                  "call_ended", "not_connected", "failed",
                  # Android companion
                  "sms_submitted", "sms_failed", "call_started", "speaker_playing", "speaker_done"}


def local_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packet is sent
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def parse_fix(body, token, now=None):
    """Validate one POST body. Returns a Location or raises ValueError."""
    data = json.loads(body)
    if not isinstance(data, dict) or not secrets.compare_digest(str(data.get("token", "")), token):
        raise PermissionError("bad token")
    lat, lon = float(data["lat"]), float(data["lon"])
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("coordinates out of range")
    acc = data.get("accuracy")
    acc = float(acc) if acc not in (None, "") else None
    if acc is not None and not (0 <= acc < 100000):
        acc = None
    address = str(data.get("address") or "").strip()[:120]
    return Location("phone", address, lat, lon, acc, time.time() if now is None else now)


class PhoneBridge:
    def __init__(self, token, port=8765, host="0.0.0.0"):
        if not token or len(token) < 6:
            raise ValueError("phone_bridge.token must be at least 6 characters")
        self.token, self.port, self.host = token, int(port), host
        self.lock = threading.Lock()
        self.fix = None
        self.updates = 0
        self.server = None
        self.error = ""
        self.pending = None          # request waiting for the phone to pick up
        self.statuses = {}           # request id -> latest status from the phone
        self.phone_seen = 0.0        # last time the phone app polled
        self.contacts = None         # phone contacts, memory only (None = never synced)
        self.sos_number = ""         # SOS demo number set in the phone app (memory only)
        self.profile = {}            # {"name", "description"} typed in the phone app (memory only)
        self.contacts_at = 0.0

    # ----- emergency request hand-off (used by caller.PhoneAppCaller) -----
    def offer(self, request_id, number, label, script, map_link=""):
        with self.lock:
            self.pending = {"id": request_id, "number": number, "label": label,
                            "script": script, "map": map_link, "created": time.time()}
            self.statuses[request_id] = "waiting_phone"

    def take_request(self):
        with self.lock:
            self.phone_seen = time.time()
            p = self.pending
            if p is None:
                return None
            age = time.time() - p["created"]
            if age > REQUEST_TTL:
                self.statuses[p["id"]] = "expired"
                self.pending = None
                return None
            return {k: p[k] for k in ("id", "number", "label", "script", "map")} | {"age": round(age, 1)}

    def report(self, request_id, status):
        if status not in PHONE_STATUSES:
            raise ValueError("unknown status")
        with self.lock:
            if request_id not in self.statuses:
                raise KeyError(request_id)
            if self.pending and self.pending["id"] == request_id:
                self.pending = None   # delivered once; never offered again
            self.statuses[request_id] = status

    def request_status(self, request_id):
        with self.lock:
            st = self.statuses.get(request_id, "unknown")
            p = self.pending
            if p and p["id"] == request_id and time.time() - p["created"] > REQUEST_TTL:
                self.pending = None
                st = self.statuses[request_id] = "expired"
            return st

    def set_contacts(self, items):
        """Validate a phone contact sync. Numbers must be full international format."""
        from .safety import E164, is_emergency_code, normalize
        out, seen = [], set()
        for c in items[:MAX_CONTACTS]:
            if not isinstance(c, dict):
                continue
            name = str(c.get("name") or "").strip()[:60]
            number = normalize(str(c.get("number") or ""))
            if not name or not E164.match(number) or is_emergency_code(number) or (name, number) in seen:
                continue
            seen.add((name, number))
            out.append({"name": name, "number": number, "starred": bool(c.get("starred"))})
        out.sort(key=lambda c: (not c["starred"], c["name"].lower()))
        with self.lock:
            self.contacts, self.contacts_at = out, time.time()
        return len(out)

    def set_sos_number(self, number):
        """Raises ValueError for short codes / emergency numbers / malformed numbers."""
        from .safety import check_test_number
        value = check_test_number(number)
        with self.lock:
            self.sos_number = value
        return value

    def set_profile(self, name=None, description=None):
        """Wearer's name / appearance typed in the phone app. Plain text, length-limited."""
        def clean(v, limit):
            v = "".join(ch for ch in str(v) if ch.isprintable()).strip()
            return v[:limit]
        with self.lock:
            if name is not None:
                self.profile["name"] = clean(name, 40)
            if description is not None:
                self.profile["description"] = clean(description, 300)

    def phone_profile(self):
        with self.lock:
            return dict(self.profile)

    def phone_sos_number(self):
        with self.lock:
            return self.sos_number

    def phone_contacts(self):
        with self.lock:
            return None if self.contacts is None else list(self.contacts)

    def phone_online(self):
        return time.time() - self.phone_seen < 5

    def latest(self):
        with self.lock:
            return self.fix

    def fresh(self, max_age=STALE_SECONDS):
        fix = self.latest()
        if fix is None or time.time() - fix.fetched_at > max_age:
            return None
        return fix

    def url(self):
        port = self.server.server_address[1] if self.server is not None else self.port
        return f"http://{local_ip()}:{port}/location"

    def start(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # never log coordinates or tokens
                pass

            def reply(self, code, payload):
                raw = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def authorized(self, token):
                return secrets.compare_digest(str(token or ""), bridge.token)

            def do_GET(self):
                if self.path.split("?")[0].rstrip("/") == "/request":
                    if not self.authorized(self.headers.get("X-Reach-Token")):
                        return self.reply(403, {"ok": False, "error": "bad token"})
                    return self.reply(200, {"ok": True, "request": bridge.take_request()})
                fix = bridge.latest()
                self.reply(200, {"app": "raven-reach", "has_fix": fix is not None,
                                 "age_s": None if fix is None else round(time.time() - fix.fetched_at)})

            def do_POST(self):
                path = self.path.rstrip("/")
                if path not in {"/location", "/request/status", "/contacts", "/settings"}:
                    return self.reply(404, {"ok": False})
                length = int(self.headers.get("Content-Length") or 0)
                limit = MAX_CONTACTS_BODY if path == "/contacts" else MAX_BODY
                if not 0 < length <= limit:
                    return self.reply(413, {"ok": False})
                if path == "/settings":
                    if not self.authorized(self.headers.get("X-Reach-Token")):
                        return self.reply(403, {"ok": False, "error": "bad token"})
                    try:
                        data = json.loads(self.rfile.read(length))
                        number = str(data.get("sos_number") or "").strip()
                        if number:
                            bridge.set_sos_number(number)
                        if "wearer_name" in data or "description" in data:
                            bridge.set_profile(data.get("wearer_name"), data.get("description"))
                    except ValueError as e:
                        return self.reply(400, {"ok": False, "error": str(e)})
                    except (TypeError, AttributeError):
                        return self.reply(400, {"ok": False, "error": "bad settings"})
                    return self.reply(200, {"ok": True})
                if path == "/contacts":
                    if not self.authorized(self.headers.get("X-Reach-Token")):
                        return self.reply(403, {"ok": False, "error": "bad token"})
                    try:
                        data = json.loads(self.rfile.read(length))
                        n = bridge.set_contacts(list(data.get("contacts") or []))
                    except (ValueError, TypeError, AttributeError):
                        return self.reply(400, {"ok": False})
                    return self.reply(200, {"ok": True, "accepted": n})
                if path == "/request/status":
                    try:
                        data = json.loads(self.rfile.read(length))
                        if not self.authorized(data.get("token")):
                            return self.reply(403, {"ok": False, "error": "bad token"})
                        bridge.report(str(data["id"]), str(data["status"]))
                    except KeyError:
                        return self.reply(404, {"ok": False, "error": "unknown request"})
                    except (ValueError, TypeError, AttributeError):
                        return self.reply(400, {"ok": False, "error": "bad status"})
                    return self.reply(200, {"ok": True})
                try:
                    fix = parse_fix(self.rfile.read(length), bridge.token)
                except PermissionError:
                    return self.reply(403, {"ok": False, "error": "bad token"})
                except (ValueError, KeyError, TypeError):
                    return self.reply(400, {"ok": False, "error": "need lat, lon, token"})
                with bridge.lock:
                    bridge.fix = fix
                    bridge.updates += 1
                self.reply(200, {"ok": True})

        try:
            self.server = ThreadingHTTPServer((self.host, self.port), Handler)
        except OSError as e:
            self.error = f"port {self.port} unavailable ({e.strerror})"
            return False
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return True

    def close(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None


def pair_url(bridge):
    """What the QR code on the glasses encodes. Phone apps open it via the ravenreach:// scheme."""
    from urllib.parse import quote
    host = bridge.url().removeprefix("http://").removesuffix("/location")
    return f"ravenreach://pair?h={quote(host)}&t={quote(bridge.token)}"
