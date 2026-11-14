"""Place ONE real test call (no UI) to verify Twilio + your phone.
   python tools/test_call.py            -> calls demo_emergency_number from your config
   python tools/test_call.py --dry-run  -> only prints the message and TwiML
"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from reach.config import load_config
from reach import location
from reach.caller import make_caller, FINAL
from reach.safety import check_test_number, mask
from reach.script import build_script, build_twiml

cfg = load_config()
script = build_script(kind="emergency", name=cfg.get("wearer_name", ""), facts={"cannot_speak"},
                      location=location.resolve(cfg), share_location=True,
                      description=cfg.get("description", ""), callback=cfg.get("callback_number", ""))
print("Config:", cfg["_path"]); print("Message:", script)
if "--dry-run" in sys.argv:
    print(build_twiml(script)); sys.exit(0)
number = check_test_number(cfg.get("demo_emergency_number", ""))
caller = make_caller(cfg)
if not caller.live:
    sys.exit('call_mode is "preview". Set "call_mode": "twilio" in your private config for a real call.')
sid = caller.place(number, script); print("Calling", mask(number), "sid", sid)
status = ""
while status not in FINAL:
    time.sleep(2); new = caller.status(sid)
    if new != status: print(" ->", new); status = new
