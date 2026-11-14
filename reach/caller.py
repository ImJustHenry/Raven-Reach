"""Call backends.

TwilioCaller  – places a REAL phone call through Twilio's Voice REST API. When the
                callee answers, Twilio's neural text-to-speech reads the script.
PreviewCaller – no phone call. Walks through fake call states and reads the script
                on this computer's speaker (macOS `say`) so you can rehearse.
"""
import shutil
import subprocess
import threading
import time

from .script import build_twiml

FINAL = {"completed", "busy", "no-answer", "failed", "canceled",
         "call_ended", "not_connected", "expired"}


class PhoneAppCaller:
    """Hands the reviewed request to the Raven Reach iPhone app over Wi-Fi.

    The iPhone opens the call to the number (you tap Call once; iOS requires it),
    and when the call connects it speaks the exact script INTO the call using
    Apple's assistive AVSpeechSynthesizer.mixToTelephonyUplink.
    """
    live = True
    label = "phone app"
    can_hangup = False

    def __init__(self, bridge):
        if bridge is None or bridge.server is None:
            raise ValueError("Phone GPS/request receiver is not running")
        self.bridge = bridge

    def place(self, to, script, map_link=""):
        import uuid
        rid = uuid.uuid4().hex
        self.bridge.offer(rid, to, "", script, map_link)
        return rid

    def status(self, rid):
        return self.bridge.request_status(rid)

    def hangup(self, rid):
        raise RuntimeError("End the call on the iPhone")


class TwilioCaller:
    live = True
    can_hangup = True
    label = "LIVE · Twilio"

    def __init__(self, sid, token, from_number, voice="Polly.Joanna-Neural", repeats=2):
        if not (sid and token and from_number):
            raise ValueError("Twilio account SID, auth token and from_number are required")
        self.sid, self.token, self.from_number = sid, token, from_number
        self.voice, self.repeats = voice, repeats
        self.base = f"https://api.twilio.com/2010-04-01/Accounts/{sid}"

    def _req(self, method, path, data=None):
        import requests
        r = requests.request(method, self.base + path, data=data, auth=(self.sid, self.token), timeout=15)
        if r.status_code >= 400:
            try:
                message = r.json().get("message", r.text)
            except Exception:
                message = r.text
            raise RuntimeError(f"Twilio {r.status_code}: {message[:160]}")
        return r.json()

    def place(self, to, script, map_link=""):
        data = {"To": to, "From": self.from_number,
                "Twiml": build_twiml(script, self.voice, self.repeats)}
        return self._req("POST", "/Calls.json", data)["sid"]

    def status(self, call_sid):
        d = self._req("GET", f"/Calls/{call_sid}.json")
        return d.get("status", "unknown")

    def hangup(self, call_sid):
        self._req("POST", f"/Calls/{call_sid}.json", {"Status": "completed"})


class PreviewCaller:
    live = False
    can_hangup = True
    label = "PREVIEW · no real call"

    def __init__(self, speak_locally=True):
        self.calls = {}
        self.speak_locally = speak_locally and shutil.which("say") is not None
        self._proc = None

    def place(self, to, script, map_link=""):
        sid = f"PREVIEW{int(time.time()*1000)}"
        self.calls[sid] = {"t0": time.monotonic(), "script": script, "spoken": False, "ended": False}
        return sid

    def status(self, sid):
        c = self.calls.get(sid)
        if c is None:
            return "unknown"
        if c["ended"]:
            return "completed"
        dt = time.monotonic() - c["t0"]
        if dt < 1.5:
            return "queued"
        if dt < 5:
            return "ringing"
        if not c["spoken"]:
            c["spoken"] = True
            if self.speak_locally:
                self._proc = subprocess.Popen(["say", c["script"]])
            else:
                c["speak_until"] = time.monotonic() + 8
        if self._proc is not None and self._proc.poll() is None:
            return "in-progress"
        if c.get("speak_until", 0) > time.monotonic():
            return "in-progress"
        c["ended"] = True
        return "completed"

    def hangup(self, sid):
        if sid in self.calls:
            self.calls[sid]["ended"] = True
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()


def make_caller(config, bridge=None):
    import os
    t = config.get("twilio") or {}
    sid = os.environ.get("TWILIO_ACCOUNT_SID") or t.get("account_sid", "")
    token = os.environ.get("TWILIO_AUTH_TOKEN") or t.get("auth_token", "")
    from_number = os.environ.get("TWILIO_FROM_NUMBER") or t.get("from_number", "")
    if config.get("call_mode") == "iphone_app":
        return PhoneAppCaller(bridge)
    if config.get("call_mode", "preview") == "twilio":
        return TwilioCaller(sid, token, from_number, t.get("voice", "Polly.Joanna-Neural"),
                            int(t.get("repeats", 2)))
    return PreviewCaller(config.get("preview_speak_locally", True) is not False)


class Background:
    """Run a blocking call off the UI thread; UI polls `.done`."""
    def __init__(self, fn, *args):
        self.result = self.error = None
        self.done = False
        def run():
            try:
                self.result = fn(*args)
            except Exception as e:  # surfaced in UI
                self.error = str(e) or type(e).__name__
            finally:
                self.done = True
        threading.Thread(target=run, daemon=True).start()
