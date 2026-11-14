"""Drive the real Raven SDK simulator through the emergency flow in PREVIEW mode."""
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from raven_framework.core.run_app import RunApp
from reach.config import load_config
from reach.ui import ReachApp

out = Path(os.environ.get("SMOKE_OUT", "/tmp/reach-smoke")); out.mkdir(parents=True, exist_ok=True)
result = {}

def make():
    cfg = load_config(); cfg["call_mode"] = "preview"; cfg["preview_speak_locally"] = False
    cfg["phone_bridge"] = dict(cfg.get("phone_bridge") or {}, port=0)  # free port; don't clash with a running app
    import tempfile; cfg["recents_path"] = str(Path(tempfile.mkdtemp()) / "recents.json")
    cfg["contacts"] = cfg.get("contacts", [])[:2] + [{"name": n, "number": f"+1555555{100+i:04d}"}
                      for i, n in enumerate(["Dad", "Sam Lee", "Priya Patel", "Coach Kim"])]
    app = ReachApp(cfg)
    t0 = time.monotonic(); state = {"stage": 0}
    def shot(name): app.grab().save(str(out / f"{name}.png"))
    def done():
        timer.stop(); print(json.dumps(result)); QTimer.singleShot(100, QApplication.instance().quit)
    def step():
        s = state["stage"]
        try:
            if time.monotonic() - t0 > 40: result["error"] = "timeout"; return done()
            if s == 0 and time.monotonic() - t0 > 2:
                import requests  # act like the iPhone Shortcut sending GPS over Wi-Fi
                r = requests.post(app.bridge.url(), json={"token": app.bridge.token, "lat": 38.63661,
                                  "lon": -90.23415, "accuracy": 9, "address": "1 N Grand Blvd, St. Louis, MO"}, timeout=3)
                result["phone_gps_accepted"] = r.json().get("ok")
                shot("1-home"); app.start_emergency(); state["stage"] = 1
            elif s == 1:
                app.toggle("cannot_speak"); app.toggle("medical"); shot("2-facts"); app.prepare_review(); state["stage"] = 2
            elif s == 2 and app.script:
                app.entry_guard = None; app.tick()
                result["confirm_locked_before_reading"] = app.buttons["confirm"].is_disabled() or len(app.pages) == 1
                shot("3-review")
                for _ in app.pages: app.page_move(1)
                app.entry_guard = None; app.tick()
                result["confirm_enabled_after_reading"] = not app.buttons["confirm"].is_disabled()
                app.confirm(); state["stage"] = 3
            elif s == 3 and app.call_status == "ringing": shot("4-ringing"); state["stage"] = 4
            elif s == 4 and app.call_status == "completed":
                shot("5-done"); result["final_status"] = app.call_status; result["script"] = app.script
                result["used_phone_gps"] = "Phone GPS location" in app.script
                app.entry_guard = None; app.show("home"); shot("6-home-recents")
                result["recent_logged"] = app.recents.items[0]["outcome"] == "called"
                app.bridge.set_contacts([{"name": "Mom", "number": "+13145550111", "starred": True},
                                         {"name": "Alex Rivera", "number": "+13145550112"},
                                         {"name": "Jordan Wu", "number": "+13145550113"}])
                app.open_contacts(); shot("7-contacts")
                result["phone_contacts_shown"] = app.buttons["contact0"].label == "Mom"
                result["contacts_paged"] = "more" in app.buttons
                done()
        except Exception as e:
            import traceback; traceback.print_exc(); result["error"] = repr(e); done()
    timer = QTimer(app); timer.timeout.connect(step); timer.start(250); app._smoke = timer
    return app

if __name__ == "__main__":
    RunApp.run(make)
    sys.exit(1 if "error" in result or result.get("final_status") != "completed" or not result.get("used_phone_gps") else 0)
