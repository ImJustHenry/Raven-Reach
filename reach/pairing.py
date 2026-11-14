"""Phone-shows-QR pairing.

1. The phone app shows a QR code:  ravenreach://phone?t=<token>&n=<phone name>
2. The glasses scan it with their world camera (Raven Camera API; the Mac webcam in
   the simulator) and adopt that token.
3. The glasses announce themselves on the local network with Bonjour / mDNS as
   _ravenreach._tcp, with a TXT record id = pair_id(token). The phone browses for
   that service, picks the one whose id matches its own token, and connects. No IP
   address is typed anywhere.

The token itself is never broadcast: only a short hash of it, so other devices on
the Wi-Fi can't learn it from the announcement.
"""
import hashlib
import re
import socket
import threading
import time
from urllib.parse import parse_qs, urlparse

SERVICE = "_ravenreach._tcp.local."
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


def pair_id(token):
    """Short public identifier for a token (same formula in the phone apps)."""
    return hashlib.sha256(("raven-reach:" + token).encode()).hexdigest()[:12]


def parse_phone_qr(text):
    """Return (token, phone_name) from a phone's QR, or None if it isn't one."""
    try:
        u = urlparse((text or "").strip())
    except ValueError:
        return None
    if u.scheme != "ravenreach" or u.netloc != "phone":
        return None
    q = parse_qs(u.query)
    token = (q.get("t") or [""])[0]
    name = "".join(ch for ch in (q.get("n") or ["Phone"])[0] if ch.isprintable())[:40] or "Phone"
    if not TOKEN_RE.match(token):
        return None
    return token, name


class Announcer:
    """Advertise the glasses via mDNS so a paired phone can find them automatically."""
    def __init__(self, port, token, name="Raven Reach glasses"):
        self.port, self.token, self.name = port, token, name
        self.zc = None
        self.info = None
        self.error = ""
        self.lock = threading.Lock()

    def _info(self):
        from zeroconf import ServiceInfo
        from .phone_bridge import local_ip
        host = socket.gethostname().split(".")[0] or "raven"
        return ServiceInfo(SERVICE, f"{self.name} ({host}).{SERVICE}",
                           addresses=[socket.inet_aton(local_ip())], port=self.port,
                           properties={"id": pair_id(self.token), "v": "1"},
                           server=f"{host}-reach.local.")

    def start(self):
        def run():
            try:
                from zeroconf import Zeroconf
                with self.lock:
                    self.zc = Zeroconf()
                    self.info = self._info()
                    self.zc.register_service(self.info, allow_name_change=True)
            except Exception as e:  # never block the app on discovery problems
                self.error = f"Wi-Fi discovery off ({type(e).__name__})"
        threading.Thread(target=run, daemon=True).start()

    def retoken(self, token):
        """Re-announce with a new pairing id after the glasses scan a phone's QR."""
        self.token = token

        def run():
            try:
                with self.lock:
                    if self.zc is None:
                        return
                    if self.info is not None:
                        self.zc.unregister_service(self.info)
                    self.info = self._info()
                    self.zc.register_service(self.info, allow_name_change=True)
            except Exception as e:
                self.error = f"Wi-Fi discovery off ({type(e).__name__})"
        threading.Thread(target=run, daemon=True).start()

    def close(self):
        with self.lock:
            if self.zc is not None:
                try:
                    if self.info is not None:
                        self.zc.unregister_service(self.info)
                    self.zc.close()
                except Exception:
                    pass
                self.zc = None


class QrScanner:
    """Grab camera frames in the background and look for a phone pairing QR."""
    def __init__(self, timeout=45):
        self.timeout = timeout
        self.result = None       # (token, name)
        self.error = ""
        self.frames = 0
        self.done = False
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._stop.set()

    def _run(self):
        try:
            import cv2
            from raven_framework.peripherals.camera import Camera
            cam = Camera()
            cam.open_camera()
            if not cam.is_camera_open():
                self.error = "Camera unavailable (check camera permission)"
                return
            detector = cv2.QRCodeDetector()
            t0 = time.monotonic()
            try:
                while not self._stop.is_set() and time.monotonic() - t0 < self.timeout:
                    frame = cam.capture_camera_image()
                    if frame is None:
                        time.sleep(0.2)
                        continue
                    self.frames += 1
                    data, _, _ = detector.detectAndDecode(frame)
                    if data:
                        parsed = parse_phone_qr(data)
                        if parsed:
                            self.result = parsed
                            return
                        self.error = "That QR isn't from the Raven Reach phone app"
                    time.sleep(0.05)
                if not self._stop.is_set():
                    self.error = "No phone QR seen — try again"
            finally:
                cam.close_camera()
        except Exception as e:
            self.error = f"Camera error ({type(e).__name__})"
        finally:
            self.done = True
