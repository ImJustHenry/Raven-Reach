"""Linux/BlueZ accessory link for the iPhone Apple Notification Center Service (ANCS).

iOS publishes ANCS only to a bonded *accessory*. An accessory advertises an ANCS
service solicitation; the iPhone connects to it as GATT central and, after
bonding, exposes ANCS on that same link. The Linux side then acts as GATT
client over the incoming connection. macOS CoreBluetooth cannot do this, so the
real call link only runs on Linux (RavenOS glasses, a Linux laptop or a Pi).

STATUS: written against the documented BlueZ D-Bus API (LEAdvertisingManager1,
AgentManager1, Device1, GattService1) but NOT yet run on Linux, RavenOS or a
real iPhone. RavenOS must expose BlueZ on the system D-Bus for this to work.

Security: the running app never opens a pairing window. It only advertises and
accepts an iPhone that is already bonded (and, if configured, whose address
matches). Bonding is a deliberate, time-limited step in tools/iphone_pair.py,
using numeric comparison so the user confirms the same 6-digit code on both
devices. Only the incoming-call category is acted on; message bodies are never requested.
"""
import asyncio
import sys
import time

ANCS_SERVICE = "7905f431-b5ce-4e99-a40f-4b1e122d00d0"
BLUEZ = "org.bluez"
ADV_PATH = "/app/ravenreach/advertisement0"
AGENT_PATH = "/app/ravenreach/agent0"
DEFAULT_NAME = "Raven Reach"


def require_linux():
    if not sys.platform.startswith("linux"):
        raise ValueError("The iPhone call link needs Linux with BlueZ (for example RavenOS); "
                         "macOS cannot act as an ANCS accessory")


def valid_adapter(name):
    return isinstance(name, str) and name.startswith("hci") and name[3:].isdigit()


def advertisement_properties(name=DEFAULT_NAME):
    """Connectable LE advertisement soliciting ANCS, so iOS treats us as an accessory."""
    name = str(name or DEFAULT_NAME)[:20]
    return {"Type": "peripheral", "SolicitUUIDs": [ANCS_SERVICE], "LocalName": name}


def _plain(value):
    return value.value if hasattr(value, "value") and hasattr(value, "signature") else value


def normalize(objects):
    """GetManagedObjects reply (Variants) -> plain {path: {iface: {prop: value}}}."""
    return {path: {iface: {k: _plain(v) for k, v in props.items()}
                   for iface, props in ifaces.items()}
            for path, ifaces in objects.items()}


def find_iphone(objects, adapter_path, allowed_address="", require_bonded=True):
    """Return (device_path, device_props) of a connected iPhone exposing ANCS, else None.

    Pure function over normalized managed objects so it can be tested offline.
    """
    allowed = (allowed_address or "").strip().upper()
    prefix = adapter_path.rstrip("/") + "/dev_"
    for path, ifaces in sorted(objects.items()):
        device = ifaces.get("org.bluez.Device1")
        if device is None or not path.startswith(prefix):
            continue
        if not device.get("Connected") or not device.get("ServicesResolved"):
            continue
        if require_bonded and not (device.get("Paired") or device.get("Bonded")):
            continue
        if allowed and str(device.get("Address", "")).upper() != allowed:
            continue
        child = path + "/"
        has_ancs = any(p.startswith(child) and
                      str(i.get("org.bluez.GattService1", {}).get("UUID", "")).lower() == ANCS_SERVICE
                      for p, i in objects.items())
        if has_ancs:
            return path, device
    return None


def connected_unpaired(objects, adapter_path):
    prefix = adapter_path.rstrip("/") + "/dev_"
    return [path for path, ifaces in sorted(objects.items())
            if path.startswith(prefix) and "/" not in path[len(prefix):]
            and ifaces.get("org.bluez.Device1", {}).get("Connected")
            and not ifaces["org.bluez.Device1"].get("Paired")]


def _interfaces(name, gate=None):
    """Build dbus-fast ServiceInterface classes lazily; dbus-fast is Linux-only for bleak."""
    from dbus_fast import DBusError, PropertyAccess
    from dbus_fast.service import ServiceInterface, dbus_property, method

    props = advertisement_properties(name)

    class Advertisement(ServiceInterface):
        def __init__(self):
            super().__init__("org.bluez.LEAdvertisement1")

        @method()
        def Release(self):
            pass

        @dbus_property(access=PropertyAccess.READ)
        def Type(self) -> "s":
            return props["Type"]

        @dbus_property(access=PropertyAccess.READ)
        def SolicitUUIDs(self) -> "as":
            return props["SolicitUUIDs"]

        @dbus_property(access=PropertyAccess.READ)
        def LocalName(self) -> "s":
            return props["LocalName"]

    def rejected(why):
        return DBusError("org.bluez.Error.Rejected", why)

    class Agent(ServiceInterface):
        """Pairing agent; only answers while PairingGate's window is open."""
        def __init__(self):
            super().__init__("org.bluez.Agent1")

        @method()
        def Release(self):
            pass

        @method()
        async def RequestConfirmation(self, device: "o", passkey: "u"):
            if not await gate.confirm(device, passkey):
                raise rejected("Code not confirmed")

        @method()
        async def RequestAuthorization(self, device: "o"):
            # Just Works has no shared code; accept only inside the window and
            # only when the user explicitly agrees in the pairing tool.
            if not await gate.confirm(device, None):
                raise rejected("Pairing not approved")

        @method()
        def AuthorizeService(self, device: "o", uuid: "s"):
            if not gate.is_open():
                raise rejected("Pairing window closed")

        @method()
        def RequestPinCode(self, device: "o") -> "s":
            raise rejected("PIN pairing not supported")

        @method()
        def RequestPasskey(self, device: "o") -> "u":
            raise rejected("Passkey entry not supported")

        @method()
        def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):
            pass

        @method()
        def DisplayPinCode(self, device: "o", pincode: "s"):
            pass

        @method()
        def Cancel(self):
            pass

    return Advertisement, Agent


class PairingGate:
    """Time-limited approval. confirm_cb(device_path, passkey_or_None) -> bool (may be async)."""
    def __init__(self, confirm_cb, seconds=120, clock=time.monotonic):
        self.confirm_cb, self.clock = confirm_cb, clock
        self.deadline = clock() + max(10, min(300, seconds))

    def is_open(self):
        return self.clock() < self.deadline

    async def confirm(self, device, passkey):
        if not self.is_open():
            return False
        result = self.confirm_cb(device, passkey)
        if asyncio.iscoroutine(result):
            result = await result
        return bool(result) and self.is_open()


class BluezAccessory:
    """Advertise as an ANCS accessory and find the bonded iPhone that connects to us."""
    def __init__(self, adapter="hci0", name=DEFAULT_NAME, allowed_address=""):
        if not valid_adapter(adapter):
            raise ValueError("bluetooth_adapter must look like hci0")
        self.adapter_path = f"/org/bluez/{adapter}"
        self.name, self.allowed_address = name, allowed_address
        self.bus = None
        self.advertising = False
        self.agent = False

    async def _proxy(self, path, iface):
        intro = await self.bus.introspect(BLUEZ, path)
        return self.bus.get_proxy_object(BLUEZ, path, intro).get_interface(iface)

    async def start(self):
        require_linux()
        from dbus_fast import BusType
        from dbus_fast.aio import MessageBus
        self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        adapter = await self._proxy(self.adapter_path, "org.bluez.Adapter1")
        if not await adapter.get_powered():
            raise RuntimeError("Bluetooth adapter is powered off")
        Advertisement, _ = _interfaces(self.name)
        self.bus.export(ADV_PATH, Advertisement())
        manager = await self._proxy(self.adapter_path, "org.bluez.LEAdvertisingManager1")
        await manager.call_register_advertisement(ADV_PATH, {})
        self.advertising = True

    async def open_pairing(self, gate):
        """Register a numeric-comparison agent for the duration of the gate."""
        _, Agent = _interfaces(self.name, gate)
        self.bus.export(AGENT_PATH, Agent())
        agents = await self._proxy("/org/bluez", "org.bluez.AgentManager1")
        await agents.call_register_agent(AGENT_PATH, "DisplayYesNo")
        await agents.call_request_default_agent(AGENT_PATH)
        self.agent = True

    async def objects(self):
        root = await self._proxy("/", "org.freedesktop.DBus.ObjectManager")
        return normalize(await root.call_get_managed_objects())

    async def pair_device(self, device_path):
        device = await self._proxy(device_path, "org.bluez.Device1")
        await device.call_pair()
        await device.set_trusted(True)

    async def wait_for_iphone(self, timeout=20.0, require_bonded=True, poll=1.0):
        """Poll BlueZ until a connected iPhone with ANCS appears; returns a bleak BLEDevice."""
        from bleak.backends.device import BLEDevice
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            found = find_iphone(await self.objects(), self.adapter_path,
                                self.allowed_address, require_bonded)
            if found:
                path, props = found
                return BLEDevice(props.get("Address", ""), props.get("Name") or props.get("Alias"),
                                 {"path": path, "props": props}, props.get("RSSI", -127))
            await asyncio.sleep(poll)
        raise TimeoutError("No bonded iPhone with Apple Notification Center Service connected yet")

    async def close(self):
        if self.bus is None:
            return
        try:
            if self.advertising:
                manager = await self._proxy(self.adapter_path, "org.bluez.LEAdvertisingManager1")
                await manager.call_unregister_advertisement(ADV_PATH)
            if self.agent:
                agents = await self._proxy("/org/bluez", "org.bluez.AgentManager1")
                await agents.call_unregister_agent(AGENT_PATH)
        except Exception:
            pass  # Best effort; BlueZ also drops these when our bus connection closes.
        finally:
            self.advertising = self.agent = False
            self.bus.disconnect()
            self.bus = None
