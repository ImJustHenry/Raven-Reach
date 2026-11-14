"""Incoming iPhone calls on the glasses: see who is calling, Answer or Decline.

Real link (Linux/RavenOS only): Apple Notification Center Service (ANCS) over BLE,
the same public mechanism smartwatches use. The iPhone sends an IncomingCall
notification (caller name/number in its Title); the accessory may send the
notification's positive action (Answer) or negative action (Decline).

What this does NOT do: carry call audio. Answering connects the call on the
iPhone; you hear and speak through the iPhone or whatever headset it is routed
to. A Raven glasses call-audio (HFP) route is not established here.

SimulatedPhone: no Bluetooth. Press "I" in the app to fake a ring for demos.
"""
import asyncio
from dataclasses import dataclass, replace
import queue
import struct
import threading
import time

ANCS_SERVICE = "7905f431-b5ce-4e99-a40f-4b1e122d00d0"
NOTIFICATION_SOURCE = "9fbf120d-6301-42d9-8c58-25e699a21dbd"
CONTROL_POINT = "69d1d8f3-45e1-49a8-9821-9bbdfdaad9d9"
DATA_SOURCE = "22eac6e9-24d6-4bb5-be44-b36ace7c7bfb"

EVENT_ADDED, EVENT_MODIFIED, EVENT_REMOVED = 0, 1, 2
FLAG_SILENT, FLAG_IMPORTANT, FLAG_PRE_EXISTING, FLAG_POSITIVE, FLAG_NEGATIVE = 1, 2, 4, 8, 16
CATEGORY_INCOMING_CALL, CATEGORY_MISSED_CALL = 1, 2
CMD_GET_ATTRIBUTES, CMD_PERFORM_ACTION = 0, 2
ATTR_APP, ATTR_TITLE = 0, 1
ACTION_POSITIVE, ACTION_NEGATIVE = 0, 1
TITLE_MAX = 64
COMMAND_TTL = 4.0


@dataclass(frozen=True)
class Call:
    uid: int
    caller: str = "Unknown caller"
    app: str = ""
    state: str = "ringing"   # ringing | answering | declining | answered | declined | ended | missed
    can_answer: bool = True
    can_decline: bool = True
    simulated: bool = False
    since: float = 0.0


# ---------------- pure ANCS protocol helpers (unit tested) ----------------
def parse_notification_source(data):
    if len(data) != 8:
        raise ValueError("ANCS notification source must be 8 bytes")
    event, flags, category, count, uid = struct.unpack("<BBBBI", bytes(data))
    return {"event": event, "flags": flags, "category": category, "count": count, "uid": uid}


def get_attributes_command(uid):
    return bytes([CMD_GET_ATTRIBUTES]) + struct.pack("<I", uid) + bytes([ATTR_APP]) + \
        bytes([ATTR_TITLE]) + struct.pack("<H", TITLE_MAX)


def action_command(uid, answer):
    return bytes([CMD_PERFORM_ACTION]) + struct.pack("<I", uid) + \
        bytes([ACTION_POSITIVE if answer else ACTION_NEGATIVE])


class AttributeResponse:
    """Reassembles a (possibly fragmented) Data Source reply for APP + TITLE."""
    WANT = (ATTR_APP, ATTR_TITLE)

    def __init__(self, uid):
        self.uid, self.buffer = uid, bytearray()

    def feed(self, chunk):
        """Return {attr_id: str} once complete, else None. Raises on a mismatched reply."""
        self.buffer.extend(chunk)
        if len(self.buffer) < 5:
            return None
        if self.buffer[0] != CMD_GET_ATTRIBUTES or struct.unpack_from("<I", self.buffer, 1)[0] != self.uid:
            raise ValueError("Unexpected ANCS data source reply")
        pos, out = 5, {}
        for _ in self.WANT:
            if len(self.buffer) < pos + 3:
                return None
            attr, length = self.buffer[pos], struct.unpack_from("<H", self.buffer, pos + 1)[0]
            if len(self.buffer) < pos + 3 + length:
                return None
            out[attr] = bytes(self.buffer[pos + 3:pos + 3 + length]).decode("utf-8", "replace")
            pos += 3 + length
        return out


class CallBook:
    """Turns ANCS events into Call states. Shared by the real link and tests."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.calls = {}
        self.missed = []

    def on_source(self, ev):
        uid, cat, flags = ev["uid"], ev["category"], ev["flags"]
        if ev["event"] == EVENT_REMOVED:
            call = self.calls.get(uid)
            if call and call.state in {"ringing", "answering", "declining"}:
                final = {"answering": "answered", "declining": "declined"}.get(call.state, "ended")
                self.calls[uid] = replace(call, state=final, since=self.clock())
            return None
        if cat == CATEGORY_MISSED_CALL and ev["event"] == EVENT_ADDED and not flags & FLAG_PRE_EXISTING:
            self.missed.append(uid)
            return uid  # fetch caller name
        if cat != CATEGORY_INCOMING_CALL:
            return None
        call = self.calls.get(uid) or Call(uid, since=self.clock())
        self.calls[uid] = replace(call, can_answer=bool(flags & FLAG_POSITIVE),
                                  can_decline=bool(flags & FLAG_NEGATIVE))
        return uid if ev["event"] == EVENT_ADDED else None

    def on_attributes(self, uid, attrs):
        name = (attrs.get(ATTR_TITLE) or "").strip() or "Unknown caller"
        if uid in self.calls:
            self.calls[uid] = replace(self.calls[uid], caller=name, app=attrs.get(ATTR_APP, ""))
        if uid in self.missed:
            self.missed[self.missed.index(uid)] = name

    def current(self):
        live = [c for c in self.calls.values()]
        live.sort(key=lambda c: c.since)
        return live[-1] if live else None


# ---------------- link implementations ----------------
class PhoneBase:
    simulated = True
    label = "Phone"

    def __init__(self):
        self.lock = threading.RLock()
        self.book = CallBook()
        self.connected = False
        self.message = ""

    def snapshot(self):
        with self.lock:
            missed = [m for m in self.book.missed if isinstance(m, str)]
            return {"connected": self.connected, "message": self.message,
                    "call": self.book.current(), "missed": missed[-3:]}

    def start(self):
        pass

    def close(self):
        pass


class SimulatedPhone(PhoneBase):
    label = "SIMULATED phone"

    def __init__(self):
        super().__init__()
        self.connected = True
        self.message = "Simulated phone · press I to fake an incoming call"
        self.next_uid = 1

    def ring(self, caller="Mom (simulated)"):
        with self.lock:
            uid, self.next_uid = self.next_uid, self.next_uid + 1
            self.book.calls[uid] = Call(uid, caller, "com.apple.mobilephone", simulated=True,
                                        since=time.monotonic())

    def _set(self, uid, state):
        with self.lock:
            c = self.book.calls.get(uid)
            if c and c.state == "ringing":
                self.book.calls[uid] = replace(c, state=state, since=time.monotonic())

    def answer(self, uid):
        self._set(uid, "answered")

    def decline(self, uid):
        self._set(uid, "declined")

    def snapshot(self):
        with self.lock:
            c = self.book.current()
            if c and c.state == "ringing" and time.monotonic() - c.since > 30:
                self.book.calls[c.uid] = replace(c, state="missed")
                self.book.missed.append(c.caller)
        return super().snapshot()


class IphoneCallLink(PhoneBase):
    """ANCS over a BlueZ accessory link. Linux only. One worker thread owns BLE."""
    simulated = False
    label = "iPhone (Bluetooth)"

    def __init__(self, adapter="hci0", address=""):
        from .bluez_accessory import require_linux, valid_adapter
        require_linux()
        if not valid_adapter(adapter):
            raise ValueError("adapter must look like hci0")
        super().__init__()
        self.adapter, self.address = adapter, address
        self.commands = queue.Queue(maxsize=2)
        self.stop = threading.Event()
        self.thread = None
        self.message = "Waiting for paired iPhone…"

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=lambda: asyncio.run(self._run()), daemon=True)
            self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)

    def _request(self, uid, answer):
        with self.lock:
            c = self.book.calls.get(uid)
            if not c or c.state != "ringing" or not (c.can_answer if answer else c.can_decline):
                raise ValueError("That call is no longer ringing")
            self.book.calls[uid] = replace(c, state="answering" if answer else "declining")
        try:
            self.commands.put_nowait((uid, answer, time.monotonic()))
        except queue.Full:
            raise ValueError("Previous action still pending") from None

    def answer(self, uid):
        self._request(uid, True)

    def decline(self, uid):
        self._request(uid, False)

    def _drain(self):
        while True:
            try:
                self.commands.get_nowait()
            except queue.Empty:
                return

    async def _run(self):
        from bleak import BleakClient
        from .bluez_accessory import BluezAccessory
        accessory = BluezAccessory(self.adapter, allowed_address=self.address)
        try:
            await accessory.start()
            while not self.stop.is_set():
                self._drain()  # never replay an Answer/Decline after reconnecting
                try:
                    device = await accessory.wait_for_iphone(timeout=20)
                    async with BleakClient(device, timeout=20) as client:
                        await self._session(client)
                except Exception as error:
                    with self.lock:
                        self.connected = False
                        self.message = f"iPhone link {type(error).__name__}; retrying"
                await asyncio.sleep(2)
        finally:
            await accessory.close()

    async def _session(self, client):
        if not client.services.get_service(ANCS_SERVICE):
            raise ValueError("iPhone did not expose ANCS (pair first with tools/iphone_pair.py)")
        pending = asyncio.Queue()
        reply = {"parser": None, "done": None}

        def on_source(_, data):
            try:
                ev = parse_notification_source(data)
            except ValueError:
                return
            with self.lock:
                fetch = self.book.on_source(ev)
            if fetch is not None:
                pending.put_nowait(fetch)

        def on_data(_, data):
            parser = reply["parser"]
            if parser is None:
                return
            try:
                attrs = parser.feed(data)
            except ValueError:
                attrs = {}
            if attrs is not None and not reply["done"].done():
                reply["done"].set_result(attrs)

        await client.start_notify(DATA_SOURCE, on_data)
        await client.start_notify(NOTIFICATION_SOURCE, on_source)
        with self.lock:
            self.connected, self.message = True, "iPhone connected · calls will appear here"
        while client.is_connected and not self.stop.is_set():
            try:
                uid, answer, created = self.commands.get_nowait()
                if time.monotonic() - created <= COMMAND_TTL:
                    try:
                        await client.write_gatt_char(CONTROL_POINT, action_command(uid, answer), response=True)
                    except Exception:
                        with self.lock:  # e.g. call already gone; never retry
                            c = self.book.calls.get(uid)
                            if c and c.state in {"answering", "declining"}:
                                self.book.calls[uid] = replace(c, state="ended")
                continue
            except queue.Empty:
                pass
            try:
                uid = pending.get_nowait()
            except asyncio.QueueEmpty:
                await asyncio.sleep(0.05)
                continue
            reply["parser"] = AttributeResponse(uid)
            reply["done"] = asyncio.get_running_loop().create_future()
            try:
                await client.write_gatt_char(CONTROL_POINT, get_attributes_command(uid), response=True)
                attrs = await asyncio.wait_for(reply["done"], 3)
                with self.lock:
                    self.book.on_attributes(uid, attrs)
            except Exception:
                pass  # caller stays "Unknown caller"
            finally:
                reply["parser"] = None
        with self.lock:
            self.connected = False
            self.message = "iPhone disconnected; waiting to reconnect"


def make_phone(config):
    if config.get("phone_link", "simulated") == "iphone":
        iphone = config.get("iphone") or {}
        return IphoneCallLink(iphone.get("adapter", "hci0"), iphone.get("address", ""))
    return SimulatedPhone()
