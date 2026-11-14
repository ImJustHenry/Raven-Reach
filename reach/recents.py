"""Recent calls, kept privately next to the config (never in the repo, never uploaded)."""
import json
import time
from pathlib import Path

DEFAULT = Path.home() / ".config" / "raven-reach" / "recents.json"
LIMIT = 30


class Recents:
    def __init__(self, path=None, clock=time.time):
        self.path = Path(path) if path else DEFAULT
        self.clock = clock
        try:
            data = json.loads(self.path.read_text())
            self.items = [x for x in data if isinstance(x, dict)][:LIMIT]
        except (OSError, ValueError):
            self.items = []

    def add(self, *, kind, name, outcome, index=None):
        """kind: 'contact' | 'emergency' | 'incoming'; outcome: called/answered/declined/missed/ended."""
        self.items.insert(0, {"kind": kind, "name": str(name)[:60], "outcome": outcome,
                              "index": index, "t": self.clock()})
        del self.items[LIMIT:]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.items))
        except OSError:
            pass  # recents are a convenience; never block a call on disk errors


def when(t, now=None):
    now = time.time() if now is None else now
    dt = max(0, now - t)
    if dt < 60:
        return "now"
    if dt < 3600:
        return f"{int(dt // 60)}m ago"
    if dt < 86400:
        return f"{int(dt // 3600)}h ago"
    return time.strftime("%a %H:%M", time.localtime(t))


LABEL = {"called": "Outgoing", "answered": "Incoming", "declined": "Declined",
         "missed": "Missed", "ended": "Incoming"}
