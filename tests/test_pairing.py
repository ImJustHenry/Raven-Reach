import time
from reach.pairing import Announcer, pair_id, parse_phone_qr


def test_parse_phone_qr():
    assert parse_phone_qr("ravenreach://phone?t=ab12cd34ef&n=Henry%27s%20iPhone") == ("ab12cd34ef", "Henry's iPhone")
    assert parse_phone_qr("ravenreach://phone?t=ab12cd34ef") == ("ab12cd34ef", "Phone")
    for bad in ("", "https://x.com/?t=ab12cd34ef", "ravenreach://pair?h=1.2.3.4:1&t=ab12cd34",
                "ravenreach://phone?t=abc", "ravenreach://phone?t=bad token!!"):
        assert parse_phone_qr(bad) is None


def test_pair_id_is_stable_and_hides_token():
    assert pair_id("ab12cd34ef") == pair_id("ab12cd34ef") != pair_id("ab12cd34eg")
    assert len(pair_id("x" * 10)) == 12 and "ab12" not in pair_id("ab12cd34ef")


def test_announcer_is_discoverable_with_pair_id():
    from zeroconf import Zeroconf
    from reach.pairing import SERVICE
    a = Announcer(48765, "ab12cd34ef", name="Reach test")
    a.start()
    zc = Zeroconf()
    try:
        info = None
        for _ in range(30):
            time.sleep(0.2)
            if a.info is not None:
                info = zc.get_service_info(SERVICE, a.info.name, timeout=1000)
                if info:
                    break
        assert info is not None and info.port == 48765
        assert info.properties[b"id"].decode() == pair_id("ab12cd34ef")
    finally:
        zc.close(); a.close()


def test_scanner_reads_phone_qr_from_camera(monkeypatch):
    import numpy as np, qrcode, cv2
    import raven_framework.peripherals.camera as cammod
    from reach.pairing import QrScanner
    img = qrcode.make("ravenreach://phone?t=ab12cd34ef&n=Pixel").convert("L")
    frame = cv2.cvtColor(np.array(img, dtype=np.uint8) * (1 if np.array(img).max() > 1 else 255), cv2.COLOR_GRAY2BGR)

    class FakeCam:
        def __init__(self, *a, **k): self.open = False
        def open_camera(self): self.open = True
        def is_camera_open(self): return self.open
        def capture_camera_image(self): return frame
        def close_camera(self): self.open = False
    monkeypatch.setattr(cammod, "Camera", FakeCam)
    s = QrScanner(timeout=5); s.start()
    for _ in range(50):
        if s.done: break
        time.sleep(0.1)
    assert s.result == ("ab12cd34ef", "Pixel")
