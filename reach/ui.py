"""Raven Reach gaze UI: pick who to call, pick facts, review the exact words, confirm."""
import textwrap
import time

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QCursor, QKeySequence, QShortcut
from raven_framework.components.container import Container
from raven_framework.components.text_box import TextBox
from raven_framework.core.raven_app import RavenApp

from . import location as loc_mod
from .caller import FINAL, Background, make_caller
from .phone_calls import SimulatedPhone, make_phone
from .phone_bridge import PhoneBridge, pair_url
from .config import ensure_bridge_token, save_bridge_token
from .pairing import Announcer, QrScanner
from .recents import LABEL as RECENT_LABEL, Recents, when
from .safety import check_test_number, mask
from .script import FACT_ORDER, FACTS, build_script
from .widgets import (AMBER, BLUE, DIM, GRAY, GREEN, INK, MUTED, RED, Avatar, GazeButton,
                      QRView, ellipsize)

CW, CH = 400, 548          # compact call card inside Raven's 640x640 app area
CARD_X, CARD_Y = 120, 70

STATUS_WORDS = {
    "queued": "Dialing…",
    "initiated": "Dialing…",
    "ringing": "Ringing…",
    "in-progress": "Answered · voice message playing",
    "completed": "Call ended",
    "busy": "Line busy · not connected",
    "no-answer": "No answer",
    "failed": "Call failed",
    "canceled": "Call cancelled",
    # reported by the Raven Reach iPhone app
    "waiting_phone": "Waiting for phone app…",
    "phone_received": "Phone got the request",
    "call_prompt": "Tap Call on the phone",
    "call_connected": "Call connected",
    "speaking": "Speaking into the call…",
    "spoken": "Message spoken into call",
    "call_ended": "Call ended",
    "not_connected": "Call not connected",
    "expired": "Phone didn't pick it up (expired)",
    "sms_submitted": "Text sent · opening call…",
    "sms_failed": "Text failed · placing call…",
    "call_started": "Call started on Android",
    "speaker_playing": "Playing on phone speaker (best effort)",
    "speaker_done": "Speaker playback finished",
}


class ReachApp(RavenApp):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.dwell = max(700, min(2500, int(config.get("dwell_ms", 1100))))
        self.page = "home"
        self.buttons = {}
        self.entry_guard = None
        self.last_activation = 0.0
        self.note = ""
        # draft
        self.kind = "emergency"
        self.contact_index = 0
        self.facts = set()
        self.share_location = True
        self.location = None
        self.loc_job = None
        self.script = ""
        self.pages = []
        self.review_page = 0
        self.visited = set()
        # call
        self.place_job = None
        self.call_sid = None
        self.call_status = None
        self.call_target = ""
        self.call_error = ""
        self.status_job = None
        self.last_poll = 0.0
        # incoming phone calls
        try:
            self.phone = make_phone(config)
        except Exception as e:
            self.phone = SimulatedPhone()
            self.phone.message = f"iPhone link unavailable ({e}); using simulated phone"
        self.phone.start()
        # phone GPS over Wi-Fi
        self.bridge = None
        bcfg = config.get("phone_bridge") or {}
        if bcfg.get("enabled", True):
            try:
                self.bridge = PhoneBridge(ensure_bridge_token(config), bcfg.get("port", 8765))
                if not self.bridge.start():
                    self.note = "Phone GPS receiver: " + self.bridge.error
            except ValueError as e:
                self.note = str(e)
        self.announcer = None
        self.scanner = None
        if self.bridge is not None and self.bridge.server is not None:
            self.announcer = Announcer(self.bridge.server.server_address[1], self.bridge.token)
            self.announcer.start()
        try:
            self.caller = make_caller(config, self.bridge)
        except ValueError as e:
            from .caller import PreviewCaller
            self.caller = PreviewCaller(config.get("preview_speak_locally", True) is not False)
            self.note = f"iPhone app calling unavailable ({e}); using preview"
        self.ring_uid = None
        self.return_page = "home"
        self.arm_at = 0.0
        self.call_view = None
        self.call_started_at = time.monotonic()
        self.recents = Recents(config.get("recents_path"))
        self.logged_uids = set()
        self.contact_page = 0
        self.chosen = None

        self.app.use_gradient_border = False
        self.app.update_background_style("transparent", None, 0, 0, "transparent")
        self.build()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(200)
        self.shortcuts = []
        for key, fn in (("E", self.start_emergency), ("C", self.open_contacts),
                        ("Escape", self.cancel_home), ("I", self.fake_ring)):
            s = QShortcut(QKeySequence(key), self)
            s.activated.connect(fn)
            self.shortcuts.append(s)

    # ---------- helpers ----------
    def text(self, value, x, y, w, h, size, color=INK, wrap=True, align="left", bold=False):
        t = TextBox(text=value, parent=self.panel, width=w, height=h, font_size=size,
                    text_color=color, wrap_words=wrap)
        t.setTextFormat(Qt.TextFormat.PlainText)
        horiz = {"left": Qt.AlignmentFlag.AlignLeft, "center": Qt.AlignmentFlag.AlignHCenter,
                 "right": Qt.AlignmentFlag.AlignRight}[align]
        t.setAlignment(horiz | Qt.AlignmentFlag.AlignTop)
        if bold:
            f = t.font(); f.setBold(True); t.setFont(f)
        t.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        t.move(x, y)
        t.show()
        return t

    def button(self, key, style, x, y, w, h, fn, **kw):
        b = GazeButton(key, style, w, h, self.dwell, **kw)
        b.setParent(self.panel)
        b.move(x, y)

        def clicked():
            now = time.monotonic()
            if (self.entry_guard is not None or now < self.arm_at
                    or now - self.last_activation < 0.5 or b.is_disabled()):
                return
            self.last_activation = now
            b.selected_until = now + 0.4
            # Re-arm: nothing on the next page fires until gaze leaves this spot.
            self.entry_guard = QRect(b.mapToGlobal(QPoint(0, 0)), b.size())
            fn()
        b.on_clicked(clicked)
        b.show()
        self.buttons[key] = b
        return b

    def round(self, key, x, y, fn, icon_name="", color=GRAY, label="", text="", size=100):
        return self.button(key, "round", x, y, size, size + 28, fn, icon_name=icon_name,
                           color=color, label=label, text=text)

    def avatar(self, x, y, size, name="", color=None, ringing=False, icon_name=""):
        a = Avatar(self.panel, size, name, color, ringing, icon_name)
        a.move(x - a.pad, y - a.pad)
        a.show()
        return a

    def enable(self, key, value):
        b = self.buttons.get(key)
        if b is not None and b.is_disabled() == bool(value):
            b.set_enabled(bool(value))

    def show(self, page):
        self.page = page
        self.note = ""
        self.build()
        self.tick()

    def who(self):
        """(display name, masked number, avatar colour, avatar icon) for the current draft."""
        number, label = self.target_number()
        if self.kind == "emergency":
            return "Emergency (demo)", mask(number), RED, "phone"
        return label, mask(number), None, ""

    # ---------- pages ----------
    def build(self):
        self.app.clear()
        self.buttons = {}
        self.avatar_w = None
        self.panel = Container(width=CW, height=CH, background_color="#0A0E11", corner_radius=36,
                               border_width=1, border_color="#1C252B", use_gradient_border=False)
        self.app.add(self.panel, x=CARD_X, y=CARD_Y)
        self.top_left = self.text("REACH", 24, 18, 80, 20, 13, MUTED, wrap=False)
        self.top_right = self.text("", 100, 18, 276, 20, 13, MUTED, wrap=False, align="right")
        p = self.page
        if p == "home":
            self.title = self.text("Recents", 22, 44, 220, 40, 30, bold=True)
            if self.call_target:
                self.button("status", "pill", 238, 48, 142, 38, lambda: self.show("status"),
                            label="Call status", icon_name="pulse")
            self.recent_rows = self.recents.items[:3]
            if not self.recent_rows:
                self.text("No recent calls yet.\nOpen Contacts to place one.", 24, 150, 352, 60, 16,
                          MUTED, align="center")
            for i, r in enumerate(self.recent_rows):
                missed = r["outcome"] == "missed"
                em = r["kind"] == "emergency"
                self.button(f"rec{i}", "row", 20, 98 + i * 68, 360, 62, lambda r=r: self.redial(r),
                            label="Emergency (demo)" if em else r["name"],
                            avatar=None if not em else "SOS",
                            sub=f"{RECENT_LABEL.get(r['outcome'], 'Call')} · {when(r['t'])}",
                            icon_name="phone", color=RED if em else GREEN,
                            title_color=RED if missed else None)
            self.round("contacts", 20, 312, self.open_contacts, "person", GRAY, "Contacts")
            self.round("emergency", 150, 312, self.start_emergency, color=RED, label="Emergency", text="SOS")
            self.round("preview", 280, 312, lambda: self.show("about"), "wifi", GRAY, "Phone link")
            self.status = self.text("", 24, 450, 352, 40, 14, MUTED, align="center")
            num = mask(self.sos_number())
            self.footer = self.text(f"SOS calls your demo line {num} — never real emergency services",
                                    24, 492, 352, 40, 12, DIM, align="center")
        elif p == "contacts":
            self.button("back", "pill", 20, 46, 96, 38, lambda: self.show("home"), label="Back", icon_name="chev_l")
            self.title = self.text("Contacts", 130, 40, 250, 34, 26, bold=True)
            self.text(self.contacts_source(), 132, 74, 250, 18, 13, MUTED, wrap=False)
            contacts = self.contacts()
            per = 5
            pages = max(1, -(-len(contacts) // per))
            self.contact_page = min(self.contact_page, pages - 1)
            start = self.contact_page * per
            if not contacts:
                self.text("No contacts yet.\nAdd them to \"contacts\" in your config.", 24, 150, 352, 60, 16,
                          MUTED, align="center")
            for j, c in enumerate(contacts[start:start + per]):
                i = start + j
                self.button(f"contact{i}", "row", 20, 98 + j * 70, 360, 64, lambda i=i: self.start_contact(i),
                            label=c.get("name", "Contact"), sub=f"mobile  {mask(c.get('number', ''))}",
                            icon_name="phone", color=GREEN)
            if pages > 1:
                self.button("less", "pill", 20, 456, 120, 44, lambda: self.contacts_more(-1),
                            label="Prev", icon_name="chev_l")
                self.text(f"{self.contact_page + 1} / {pages}", 150, 468, 100, 22, 15, MUTED, align="center")
                self.button("more", "pill", 260, 456, 120, 44, lambda: self.contacts_more(1),
                            label="Next", icon_name="chev_r")
            self.status = self.text("", 24, 508, 352, 24, 13, MUTED, align="center")
        elif p in {"facts", "review"}:
            name, num, color, ic = self.who()
            self.avatar(22, 46, 52, name, color, icon_name=ic)
            self.title = self.text(name if p == "facts" else "Review message", 88, 46, 290, 30, 22, bold=True)
            self.subtitle = self.text(num if p == "facts" else f"to {name} · {num}", 88, 76, 290, 22, 14, MUTED)
            if p == "facts":
                self.text("What's true right now?", 24, 118, 352, 20, 14, MUTED)
                for i, k in enumerate(FACT_ORDER):
                    self.button(k, "chip", 20 + (i % 2) * 186, 144 + (i // 2) * 62, 174, 54,
                                lambda k=k: self.toggle(k), label=FACTS[k][0])
                self.button("share_location", "chip", 20, 268, 360, 54, self.toggle_location,
                            label="Share my phone's location")
                self.status = self.text("", 24, 332, 352, 40, 13, MUTED, align="center")
                self.round("cancel", 60, 388, self.cancel_home, "x", GRAY, "Cancel")
                self.round("review", 240, 388, self.prepare_review, "next", BLUE, "Review")
            else:
                self.message = self.text("", 20, 112, 360, 214, 17, wrap=False)
                self.message.setStyleSheet(f"background:#12181C; border-radius:18px; padding:14px; color:{INK};")
                self.status = self.text("", 24, 332, 352, 20, 13, MUTED, align="center", wrap=False)
                self.button("prev", "pill", 20, 360, 172, 46, lambda: self.page_move(-1),
                            label="Previous", icon_name="chev_l")
                self.button("next", "pill", 208, 360, 172, 46, lambda: self.page_move(1),
                            label="Next", icon_name="chev_r")
                self.round("cancel", 60, 416, self.cancel_home, "x", GRAY, "Cancel")
                self.round("confirm", 240, 416, self.confirm, "phone",
                           RED if self.kind == "emergency" else GREEN,
                           "Call SOS" if self.kind == "emergency" else "Call")
        elif p in {"status", "hangup"}:
            name = (self.call_target or "No call yet").split(" (")[0]
            em = name.startswith("DEMO emergency")
            self.avatar(148, 50, 104, name, RED if em else None, icon_name="phone" if em else "")
            self.title = self.text("Emergency (demo)" if em else name, 20, 168, 360, 34, 26,
                                   align="center", bold=True)
            self.subtitle = self.text("", 20, 206, 360, 22, 16, MUTED, align="center", wrap=False)
            self.status = self.text("", 28, 240, 344, 150, 14, MUTED, align="center")
            if p == "status":
                self.round("home", 60, 404, lambda: self.show("home"), "home", GRAY, "Home")
                self.round("hangup", 240, 404, lambda: self.show("hangup"), "hangup", RED, "End")
            else:
                self.round("keep", 60, 404, lambda: self.show("status"), "back", GRAY, "Keep call")
                self.round("hangup_confirm", 240, 404, self.hangup, "hangup", RED, "End call")
        elif p == "incoming":
            c = self.call_view if (self.call_view and self.call_view.uid == self.ring_uid) else None
            name = c.caller if c else "Unknown caller"
            self.subtitle = self.text("Incoming call", 20, 46, 360, 22, 15, MUTED, align="center")
            self.avatar_w = self.avatar(148, 84, 104, name, ringing=True)
            self.title = self.text(name, 20, 218, 360, 36, 28, align="center", bold=True)
            self.status = self.text("", 28, 258, 344, 110, 14, MUTED, align="center")
            self.round("decline", 40, 388, self.decline_call, "hangup", RED, "Decline")
            self.round("answer", 260, 388, self.answer_call, "phone", GREEN, "Accept")
            self.button("ignore", "pill", 150, 420, 100, 44, self.leave_call_page, label="Ignore")
        elif p == "scan":
            self.title = self.text("Scan your phone", 22, 44, 356, 40, 26, bold=True)
            self.avatar(148, 120, 104, "", BLUE, icon_name="qr")
            self.text("Open Raven Reach on your phone and hold its QR code in front of the glasses.",
                      28, 250, 344, 60, 16, INK, align="center")
            self.status = self.text("", 28, 318, 344, 60, 14, MUTED, align="center")
            self.round("cancel", 150, 404, self.cancel_scan, "x", GRAY, "Cancel")
        else:  # about / phone link
            self.title = self.text("Pair your phone", 22, 44, 356, 40, 26, bold=True)
            if self.bridge is not None and self.bridge.server is not None:
                qr = QRView(self.panel, 196, pair_url(self.bridge))
                qr.move(102, 92); qr.show()
                self.text("Best: tap Scan phone and look at the QR in your phone's Raven Reach app. "
                          "Or scan this code with the phone.", 24, 294, 352, 36, 13, MUTED, align="center")
            self.status = self.text("", 24, 332, 352, 70, 13, INK, align="center")
            self.round("home", 60, 404, lambda: self.show("home"), "home", GRAY, "Home")
            self.round("scan", 240, 404, self.start_scan, "qr", BLUE, "Scan phone")

    # ---------- actions ----------
    def target_number(self):
        if self.kind == "emergency":
            return self.sos_number(), "DEMO emergency line"
        c = self.chosen or {}
        return c.get("number", ""), c.get("name", "contact")

    def start_emergency(self):
        if self.busy_call():
            self.show("status")
            return
        self.kind, self.facts, self.share_location = "emergency", set(), True
        self.show("facts")

    def start_contact(self, i):
        contacts = self.contacts()
        if i >= len(contacts):
            return
        # Freeze who was picked: a later phone re-sync can't swap the recipient mid-flow.
        self.chosen = dict(contacts[i])
        self.kind, self.contact_index, self.facts, self.share_location = "contact", i, set(), False
        self.show("facts")

    def contacts(self):
        """Phone contacts when the phone app has synced them, else the config list."""
        synced = self.bridge.phone_contacts() if self.bridge is not None else None
        if synced:
            return synced
        return self.config.get("contacts") or []

    def profile(self, key, config_key):
        """Name / appearance typed in the phone app if set there, else the config."""
        p = self.bridge.phone_profile() if self.bridge is not None else {}
        return p.get(key) or self.config.get(config_key, "")

    def sos_number(self):
        """SOS demo number from the phone app if set there, else the config."""
        phone = self.bridge.phone_sos_number() if self.bridge is not None else ""
        return phone or self.config.get("demo_emergency_number", "")

    def contacts_source(self):
        synced = self.bridge.phone_contacts() if self.bridge is not None else None
        return f"From your phone · {len(synced)}" if synced else "From config"

    def open_contacts(self):
        self.contact_page = 0
        self.show("contacts")

    def contacts_more(self, step=1):
        self.contact_page = max(0, self.contact_page + step)
        self.show("contacts")

    def redial(self, r):
        """Call back from Recents. Always goes through facts + review; never dials instantly."""
        if self.busy_call():
            return self.show("status")
        if r["kind"] == "emergency":
            return self.start_emergency()
        idx = self.contact_for(r)
        if idx is not None:
            self.start_contact(idx)

    def contact_for(self, r):
        contacts = self.contacts()
        for j, c in enumerate(contacts):
            if c.get("name", "").strip().lower() == r["name"].strip().lower():
                return j
        return None

    def toggle(self, key):
        self.facts ^= {key}
        self.tick()

    def toggle_location(self):
        self.share_location = not self.share_location
        self.tick()

    def prepare_review(self):
        self.script = ""
        self.pages, self.review_page, self.visited = [], 0, set()
        self.show("review")
        if self.share_location:
            self.loc_job = Background(loc_mod.resolve, self.config, self.bridge)
        else:
            self.loc_job = None
            self.finish_script(None)

    def finish_script(self, location):
        self.location = location
        number, label = self.target_number()
        self.script = build_script(
            kind=self.kind, name=self.profile("name", "wearer_name"), facts=self.facts,
            location=location, share_location=self.share_location,
            description=self.profile("description", "description"),
            callback=self.config.get("callback_number", ""),
            recipient_label="" if self.kind == "emergency" else label)
        lines = textwrap.wrap(self.script, width=36)
        self.pages = ["\n".join(lines[i:i + 7]) for i in range(0, len(lines), 7)] or ["(empty)"]
        self.review_page, self.visited = 0, {0}

    def page_move(self, step):
        if self.pages:
            self.review_page = max(0, min(len(self.pages) - 1, self.review_page + step))
            self.visited.add(self.review_page)
        self.tick()

    def confirm(self):
        if not self.script or not set(range(len(self.pages))) <= self.visited:
            self.note = "Read every page first."
            return
        number, label = self.target_number()
        try:
            number = check_test_number(number)
        except ValueError as e:
            self.note = str(e)
            return
        self.call_sid, self.call_status, self.call_error = None, "placing", ""
        self.texted = False
        self.call_target = f"{label} ({mask(number)})"
        self.recents.add(kind=self.kind, name=label, outcome="called",
                         index=self.contact_index if self.kind == "contact" else None)
        link = self.location.map_link() if (self.share_location and self.location) else ""
        self.place_job = Background(self.caller.place, number, self.script, link)
        self.show("status")

    def busy_call(self):
        return self.place_job is not None or (self.call_sid and self.call_status not in FINAL)

    def poll_now(self):
        if self.call_sid and self.status_job is None:
            self.status_job = Background(self.caller.status, self.call_sid)
            self.last_poll = time.monotonic()

    def hangup(self):
        if self.call_sid and self.call_status not in FINAL:
            Background(self.caller.hangup, self.call_sid)
        self.show("status")

    def cancel_home(self):
        # Cancels an unsent draft only. Never ends an active call.
        self.script, self.pages, self.facts = "", [], set()
        self.loc_job = None
        self.show("home")

    # ---------- phone shows QR, glasses scan it ----------
    def start_scan(self):
        if self.scanner is not None:
            self.scanner.stop()
        self.scanner = QrScanner()
        self.scanner.start()
        self.show("scan")

    def cancel_scan(self):
        if self.scanner is not None:
            self.scanner.stop()
            self.scanner = None
        self.show("about")

    def adopt_phone_token(self, token, name):
        """Use the phone's token; announce the new pairing id so the phone finds us."""
        self.bridge.token = token
        save_bridge_token(self.config, token)
        if self.announcer is not None:
            self.announcer.retoken(token)
        self.show("about")
        self.note = f"Paired with {name}. The phone will find the glasses on Wi-Fi in a few seconds."

    # ---------- incoming phone calls ----------
    def fake_ring(self):
        if isinstance(self.phone, SimulatedPhone):
            self.phone.ring()

    def answer_call(self):
        try:
            self.phone.answer(self.ring_uid)
        except Exception as e:
            self.note = str(e)

    def decline_call(self):
        try:
            self.phone.decline(self.ring_uid)
        except Exception as e:
            self.note = str(e)

    def leave_call_page(self):
        self.show(self.return_page if self.return_page != "incoming" else "home")

    def watch_phone(self):
        snap = self.phone.snapshot()
        call = snap["call"]
        self.call_view = call
        if call is not None and call.state in {"answered", "declined", "missed", "ended"} \
                and call.uid not in self.logged_uids:
            self.logged_uids.add(call.uid)
            self.recents.add(kind="incoming", name=call.caller, outcome=call.state)
        if call is not None and call.state == "ringing" and call.uid != self.ring_uid:
            self.ring_uid = call.uid
            if self.page != "incoming":
                self.return_page = self.page
            # A popup can appear under a resting gaze: hold all targets for a moment
            # so nobody answers or declines by accident.
            self.arm_at = time.monotonic() + 1.2
            self.show("incoming")
            return snap
        return snap

    # ---------- refresh loop ----------
    def tick(self):
        if self.entry_guard is not None and not self.entry_guard.contains(QCursor.pos()):
            self.entry_guard = None
        # background jobs
        if self.loc_job is not None and self.loc_job.done:
            job, self.loc_job = self.loc_job, None
            if self.page == "review":
                self.finish_script(job.result)
        if self.place_job is not None and self.place_job.done:
            job, self.place_job = self.place_job, None
            if job.error:
                self.call_status, self.call_error = "failed", job.error
            else:
                self.call_sid, self.call_status = job.result, "queued"
        if self.status_job is not None and self.status_job.done:
            job, self.status_job = self.status_job, None
            if job.error:
                self.call_error = job.error
            else:
                self.call_status, self.call_error = job.result, ""
        if self.call_sid and self.call_status not in FINAL and time.monotonic() - self.last_poll > 2:
            self.poll_now()
        if self.scanner is not None and self.scanner.done:
            sc, self.scanner = self.scanner, None
            if sc.result and self.bridge is not None:
                self.adopt_phone_token(*sc.result)
            elif self.page == "scan":
                self.note = sc.error or "Scan stopped"
        self.phone_snap = self.watch_phone()
        self.repaint_ui()

    def repaint_ui(self):
        live = self.caller.live
        snap = getattr(self, "phone_snap", None) or self.phone.snapshot()
        if self.caller.label == "phone app" and self.bridge is not None:
            phone_ok = self.bridge.phone_online()
        else:
            phone_ok = snap["connected"]
        fix = self.bridge.latest() if self.bridge else None
        if fix is None:
            gps = "GPS –"
        else:
            age = int(time.time() - fix.fetched_at)
            gps = f"GPS {age}s" if age <= 120 else "GPS stale"
        bits = ["● phone" if phone_ok else "○ phone", gps]
        if not live:
            bits.insert(0, "PREVIEW")
        self.top_right.set_text("  ·  ".join(bits))
        p = self.page
        status = self.note
        if p == "home":
            for i, r in enumerate(getattr(self, "recent_rows", [])):
                ok = r["kind"] == "emergency" or self.contact_for(r) is not None
                self.enable(f"rec{i}", ok and not self.busy_call())
            if self.busy_call():
                status = status or f"On a call · {STATUS_WORDS.get(self.call_status, self.call_status)}"
            if snap["missed"]:
                status = status or "Missed: " + ", ".join(snap["missed"])
            if self.phone.simulated and self.caller.label != "phone app":
                status = status or "Press I to simulate an incoming call"
        elif p == "contacts":
            for k in self.buttons:
                if k.startswith("contact"):
                    self.enable(k, not self.busy_call())
            pages = max(1, -(-len(self.contacts()) // 5))
            self.enable("less", self.contact_page > 0)
            self.enable("more", self.contact_page < pages - 1)
            if self.busy_call():
                status = status or "Finish the current call first"
        elif p == "facts":
            for k in FACT_ORDER:
                b = self.buttons[k]
                if b.on != (k in self.facts):
                    b.on = k in self.facts
            self.buttons["share_location"].on = self.share_location
            status = status or ("Only what you select is said. "
                                + ("Your profile note is included." if self.profile("description", "description") else ""))
        elif p == "review":
            if not self.script:
                self.message.set_text("Preparing message…\nLooking up your phone's location.")
                for k in ("prev", "next", "confirm"):
                    self.enable(k, False)
            else:
                self.message.set_text(self.pages[self.review_page])
                all_read = set(range(len(self.pages))) <= self.visited
                self.enable("prev", self.review_page > 0)
                self.enable("next", self.review_page < len(self.pages) - 1)
                self.enable("confirm", all_read and not self.busy_call())
                dots = " ".join("●" if i == self.review_page else "○" for i in range(len(self.pages)))
                status = status or (dots + ("   ready" if all_read else "   read every page to unlock Call"))
        elif p in {"status", "hangup"}:
            word = "Calling…" if self.call_status == "placing" else \
                STATUS_WORDS.get(self.call_status or "", self.call_status or "No call yet")
            if not hasattr(self, "call_started_at") or self.call_status == "placing":
                self.call_started_at = time.monotonic()
            timer = "" if not self.call_sid or self.call_status in FINAL else \
                "  ·  " + ellipsize(time.monotonic() - self.call_started_at)
            self.subtitle.set_text(("PREVIEW · " if not live else "") + word + timer)
            good = self.call_status in {"in-progress", "call_connected", "speaking", "spoken", "call_started"}
            self.subtitle.setStyleSheet(f"color:{GREEN if good else MUTED};")
            lines = []
            if self.call_target:
                lines.append(self.call_target.split(" (")[-1].rstrip(")"))
            if self.call_status in {"in-progress", "speaking"}:
                lines.append("The voice message is playing on the call.")
            if self.call_status in {"completed", "spoken", "call_ended"}:
                lines.append("Message played if answered. That doesn't prove it was understood "
                             "or that help is coming.")
            if self.caller.label == "phone app":
                if self.call_status == "waiting_phone" and not self.bridge.phone_online():
                    lines.append("Open the Raven Reach app on your phone.")
                if self.call_status == "sms_submitted" or getattr(self, "texted", False):
                    self.texted = True
                    lines.append("Message + map link texted to the number.")
                if self.call_status in {"call_started", "speaker_playing", "speaker_done"}:
                    lines.append("Android: speaker playback is best effort.")
                if self.call_sid and self.call_status not in FINAL:
                    lines.append("End the call on the phone.")
            if self.call_error:
                lines.append("Error: " + self.call_error)
            if not live:
                lines.append("Preview: no phone rang; the voice played on this computer.")
            if p == "hangup":
                lines.insert(0, "End this call? Looking away never ends a call.")
            status = "\n".join(lines) if not self.note else self.note
            active = bool(self.call_sid) and self.call_status not in FINAL and self.caller.can_hangup
            self.enable("hangup", active)
            self.enable("hangup_confirm", active)
        elif p == "incoming":
            c = self.call_view if (self.call_view and self.call_view.uid == self.ring_uid) else None
            state = c.state if c else "ended"
            self.subtitle.set_text({"ringing": "Incoming call", "answering": "Answering…",
                                    "declining": "Declining…", "answered": "Connected on your phone",
                                    "declined": "Declined", "missed": "Missed call"}.get(state, "Call ended"))
            if self.avatar_w is not None:
                self.avatar_w.ringing = state == "ringing"
                self.avatar_w.update()
            lines = []
            if c and c.app and c.app != "com.apple.mobilephone":
                lines.append(f"via {c.app}")
            if c and c.simulated:
                lines.append("Simulated call")
            if state == "answered":
                lines.append("Talk on your phone or its headset — glasses audio isn't set up yet.")
            elif state == "ended":
                lines.append("Stopped ringing (answered on the phone, declined, or caller hung up).")
            ringing = state == "ringing"
            self.enable("answer", ringing and (c.can_answer if c else False))
            self.enable("decline", ringing and (c.can_decline if c else False))
            self.enable("ignore", True)
            self.buttons["ignore"].label = "Ignore" if ringing else "Back"
            status = status or "\n".join(lines)
        elif p == "scan":
            sc = self.scanner
            status = status or ("Looking… (camera frames: %d)" % sc.frames if sc else "")
        else:
            if self.bridge is None or self.bridge.server is None:
                status = "Phone link is off. " + (self.bridge.error if self.bridge else "")
            else:
                fix = self.bridge.latest()
                got = "No GPS received yet" if fix is None else f"Last GPS: {fix.short()}"
                addr = self.bridge.url().removeprefix('http://').removesuffix('/location')
                link = "● Phone connected" if self.bridge.phone_online() else "○ Waiting for phone"
                status = status or (f"{link}  ·  SOS {mask(self.sos_number())} "
                          f"({'phone' if self.bridge.phone_sos_number() else 'config'})\n{got}\n"
                          f"Manual: {addr}  token {self.bridge.token}")
        if time.monotonic() < self.arm_at:
            for b in self.buttons.values():
                b.set_enabled(False)
            if p == "incoming":
                status = "Controls arm in a moment"
        elif self.entry_guard is not None:
            for b in self.buttons.values():
                b.set_enabled(False)
        elif p in {"home", "contacts", "facts", "about", "status", "hangup", "scan"}:
            for k, b in self.buttons.items():
                if (k not in {"hangup", "hangup_confirm", "less", "more"} and not k.startswith(("rec", "contact"))
                        and b.is_disabled()):
                    b.set_enabled(True)
        elif p == "review":
            self.enable("cancel", True)
        self.status.set_text(status)
        for b in self.buttons.values():
            b.update()

    def closeEvent(self, event):
        self.timer.stop()
        self.phone.close()
        if self.scanner is not None:
            self.scanner.stop()
        if self.announcer is not None:
            self.announcer.close()
        if self.bridge is not None:
            self.bridge.close()
        super().closeEvent(event)
