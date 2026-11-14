"""Simulated incoming call in the real Raven SDK simulator: ring -> arm delay -> answer."""
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
    cfg = load_config(); cfg["phone_link"] = "simulated"; cfg["call_mode"] = "preview"
    cfg["phone_bridge"] = dict(cfg.get("phone_bridge") or {}, port=0)  # free port; don't clash with a running app
    import tempfile; cfg["recents_path"] = str(Path(tempfile.mkdtemp()) / "recents.json")
    app = ReachApp(cfg); t0 = time.monotonic(); st = {"s": 0}
    def done():
        timer.stop(); print(json.dumps(result)); QTimer.singleShot(100, QApplication.instance().quit)
    def step():
        try:
            if time.monotonic() - t0 > 20: result["error"] = "timeout"; return done()
            s = st["s"]
            if s == 0 and time.monotonic() - t0 > 2:
                app.show("home"); app.fake_ring(); st["s"] = 1
            elif s == 1 and app.page == "incoming":
                result["popped_from"] = app.return_page
                result["answer_disarmed_at_popup"] = app.buttons["answer"].is_disabled()
                app.grab().save(str(out / "incoming.png")); st["s"] = 2
            elif s == 2 and time.monotonic() > app.arm_at + 0.3:
                app.entry_guard = None; app.tick()
                result["answer_armed_after_delay"] = not app.buttons["answer"].is_disabled()
                app.answer_call(); app.tick(); st["s"] = 3
            elif s == 3:
                result["state"] = app.call_view.state
                app.tick(); result["incoming_logged"] = bool(app.recents.items) and app.recents.items[0]["outcome"] == "answered"
                app.grab().save(str(out / "answered.png")); done()
        except Exception as e:
            import traceback; traceback.print_exc(); result["error"] = repr(e); done()
    timer = QTimer(app); timer.timeout.connect(step); timer.start(200); app._smoke = timer
    return app

if __name__ == "__main__":
    RunApp.run(make)
    ok = result.get("state") == "answered" and result.get("answer_disarmed_at_popup") and result.get("answer_armed_after_delay")
    sys.exit(0 if ok and "error" not in result else 1)
