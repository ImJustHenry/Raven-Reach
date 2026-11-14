# Raven Reach (hackathon build)

Gaze-controlled calling for Raven glasses. The wearer picks who to call and which facts are true, reads the exact message, then confirms. A real phone rings, and when someone answers a neural text-to-speech voice reads the wearer's **name, location, chosen facts and profile note** (said twice).

> **EMERGENCY is a demo.** It calls `demo_emergency_number`, which is a phone **you own**. It never calls 911, 112, 999 or any other emergency service. Short codes and known emergency numbers are refused in code (`reach/safety.py`).

## Run it (preview mode: no real calls)

```bash
cd ~/Desktop/Raven-Reach
.venv/bin/python main.py
```

The cursor stands in for your gaze: hover to dwell, or click. Keys: `E` emergency, `C` contacts, `Esc` cancel.
In preview mode the "call" goes queued → ringing → answered, and the message is read aloud on your Mac with `say`.

First-time setup, if `.venv` is missing:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ../raven-framework requests pytest pytest-qt
```

## Make your own phone ring (Twilio)

1. Create a Twilio account and buy or claim a voice-capable number. On a **trial** account, also verify your own cell under *Verified Caller IDs*. Trial calls play a short Twilio notice first and ask you to press a key, then the message plays.
2. Create your private config **outside the repo**:

```bash
mkdir -p ~/.config/raven-reach
cp config.example.json ~/.config/raven-reach/config.json
```

3. Edit `~/.config/raven-reach/config.json`:
   - `"call_mode": "twilio"`
   - `"demo_emergency_number": "+1YOURCELL"`
   - `twilio.account_sid`, `twilio.from_number` (your Twilio number)
   - Auth token: put it in the config, or better, run `export TWILIO_AUTH_TOKEN=...`
   - `wearer_name`, `description`, `location.address` / `lat` / `lon`, `contacts`
4. Test one call without the UI: `.venv/bin/python tools/test_call.py` (use `--dry-run` to only print the message).
5. Run the app: `.venv/bin/python main.py` → EMERGENCY → pick facts → Review (read every page) → CONFIRM. Your phone rings.

## Incoming iPhone calls on the glasses (Answer / Decline)

The glasses join your iPhone as a Bluetooth accessory and use Apple's **Notification Center Service (ANCS)**, the same feature smartwatches use. When the iPhone rings, Reach shows the caller's name and an **ANSWER** / **Decline** / **Ignore** screen. The buttons stay disarmed for 1.2 s after the screen appears, so a resting gaze can't answer by accident.

- **Mac (simulator):** macOS can't act as an ANCS accessory, so this is simulated. Press `I` in the app to fake an incoming call.
- **Real iPhone:** needs Linux with BlueZ, meaning the Raven glasses (RavenOS) or a Linux laptop or Raspberry Pi:
  1. `pip install bleak==0.22.3`
  2. `python tools/iphone_pair.py`, then on the iPhone: Settings → Bluetooth → tap **Raven Reach** → Pair, confirm that the 6-digit codes match, and allow notifications.
  3. In the config: `"phone_link": "iphone"`. Optionally put the printed address in `iphone.address` so only your phone is accepted.
- **Audio:** Answer picks up the call *on the iPhone*. You talk and listen on the iPhone or its headset. Routing call audio through the glasses isn't set up.
- **Status:** the ANCS code has unit tests, but it hasn't yet been run against a real iPhone.

## Pairing a phone: the glasses scan the phone's QR

1. Open the **Raven Reach** app on the phone. It shows a QR code at the top.
2. On the glasses: **Phone link → Scan phone**, then look at the phone's QR. The glasses' camera reads it (the Mac webcam in the simulator).
3. The glasses announce themselves on Wi-Fi (Bonjour / mDNS, `_ravenreach._tcp`) with a hash of the pairing code. The phone finds them by itself within a few seconds. No IP address is typed.

Fallbacks: the glasses also show their own QR code (scan it with the iPhone Camera, or *Scan the glasses' QR* on Android), and both apps have **Manual connection**. Discovery needs the phone and the glasses on the same network, and works best on a phone hotspot (some venue Wi-Fi blocks discovery).

**In both phone apps:**
- **About you:** your name and appearance/info (e.g. "red jacket, asthma, inhaler in left pocket"). Sent to the glasses and read out in the emergency message in place of `wearer_name` / `description` from the config.
- **SOS number:** the phone the SOS button calls (one you own). 911, 112, 999 and short codes are refused on the phone and on the glasses.
- **Share my contacts:** names plus one number per person, over local Wi-Fi only, held in memory on the glasses.

**Text with the call (iPhone):** iOS never lets an app send a text silently, so Raven Reach hands the message to a shortcut named **Raven Reach Text**, which can.
- One-time setup: in Shortcuts, tap **+** and name it *Raven Reach Text*. Add **Send Message**: Message = *Shortcut Input*, Recipients = your SOS number. Tap ▸ and turn **Show When Run** off. Run it once and choose **Always Allow**.
- On an emergency, the text (exact message plus map link) is sent with no prompt, Shortcuts jumps back to Raven Reach, and the call opens. You still tap **Call**, because iOS requires it.
- If the shortcut is missing, Messages opens instead and you tap Send. Android sends the text automatically with no setup.

**Voice on the call:**
- **iPhone:** after tapping **Call**, switch back to Raven Reach. It speaks automatically while it's open during the call, and **Speak message into call** speaks again. *What happened* shows each step (request received, call connected, speech started/finished, app open or in background).
- **Android:** can't speak into a call. Loudspeaker playback is on by default (best effort); the text message with the map link is the reliable channel.

## Presentation setup: Raven Reach iPhone app (no Twilio)

`ios/RavenReachPhone` is a small iPhone app. It sends the phone's GPS to the glasses, receives the emergency request you confirm on the glasses, opens the call to your demo line, and when the call connects **speaks the exact reviewed message into the call**. It uses Apple's assistive `AVSpeechSynthesizer.mixToTelephonyUplink`, so the person who answers hears it.

1. Open `ios/RavenReachPhone.xcodeproj` in Xcode. Plug in your iPhone and select it as the run destination.
2. Target → *Signing & Capabilities* → Team: your Apple ID (a free account works). If Xcode complains about the bundle ID, change it to something unique.
3. Press Run. On the iPhone, trust the developer in Settings → General → VPN & Device Management.
4. In the app, enter the glasses (or Mac) address and token from **Phone GPS setup** on the glasses, then turn on **Connect**. Allow Location (*While Using*) and Local Network access.
5. `~/.config/raven-reach/config.json`: `"call_mode": "iphone_app"`, `"demo_emergency_number": "+1<the demo phone>"`.
6. Glasses: EMERGENCY → facts → read every page → CONFIRM → iPhone shows **Call** → tap it → the demo phone answers → the message is spoken (twice). The glasses' status page follows along: *Tap Call → Call connected → Speaking → Message spoken → Call ended*.

Notes:
- iOS always asks you to tap **Call**. Apps can't dial silently.
- Keep the app open (in the foreground) when the request arrives. Background location keeps it running during the call. If you hear no speech after the call connects, switch back to the Raven Reach app.
- Each request reaches the phone **once** and expires after 60 s. The phone refuses short codes and emergency numbers.
- **Not yet verified on a real iPhone:** that the remote listener actually hears the speech. Test with your demo phone before presenting. "Message spoken" means the iPhone spoke it, not that someone confirmed hearing it.
- The app replaces the Shortcut below, which still works as a GPS-only option.

## Presentation setup: Raven Reach Android app

`android/` is the Android version. It connects to the glasses over Wi-Fi exactly like the iPhone app (same address and token) and does the same job, with one platform difference:

| | iPhone app | Android app |
|---|---|---|
| Phone GPS → glasses | ✅ | ✅ |
| Gets the confirmed request once | ✅ | ✅ |
| Places the call | you tap **Call** | **automatic** (if you grant Phone permission); otherwise tap a notification |
| Voice the person who answers hears | ✅ spoken *into* the call (Apple assistive API, still to verify on device) | ❌ Android has no public API for this. Optional **speaker playback** is best effort: echo cancellation may stop the caller hearing it |
| Text with the exact message + map link | — | ✅ sent to the same number at the same moment (reliable) |

**Install (USB):**
1. On the phone, enable Developer options → **USB debugging**, then plug it into the Mac.
2. Build and install:
   ```bash
   cd android
   export JAVA_HOME=$(ls -d ~/.local/raven-tools/jdk-17*/Contents/Home)
   ~/.local/raven-tools/gradle-8.11.1/bin/gradle --no-daemon assembleDebug
   ~/Library/Android/sdk/platform-tools/adb install -r app/build/outputs/apk/debug/app-debug.apk
   ```
   Or just copy `android/app/build/outputs/apk/debug/app-debug.apk` to the phone and open it.
3. In the app, tap **Grant permissions** and allow Location, Phone (calls and call state), SMS and Notifications. Enter the glasses address and token, then tap **Connect to glasses**.
4. The glasses config is the same as for iPhone: `"call_mode": "iphone_app"` (also accepted: `"android_app"`).

On the glasses, the status page shows: *Phone got the request → Text sent · placing call → Call started → (Playing on speaker) → Call ended*. "Call started" means the phone went off-hook; Android doesn't tell normal apps when the other side answers.

**Contacts come from the phone.** With *Share my contacts with the glasses* checked and the Contacts permission granted, the app sends names and one number per person (primary, else mobile) to the glasses over local Wi-Fi every 2 minutes. Favorites come first. The glasses keep them in memory only, never on disk, and the Contacts screen shows "From your phone · N". Numbers that can't be put in `+1…` format, short codes and emergency numbers are skipped. The `contacts` list in the config is only a fallback for when no phone is connected.

**Not yet run on a real Android phone.** It compiles, and its 3 unit tests pass, but its Wi-Fi connection, SMS and calls haven't been exercised on a device.

## Phone GPS → glasses (iPhone Shortcut, no app needed)

The Reach app listens on your Wi-Fi for the phone's GPS. Open **Phone GPS setup** in the app to see the address and token, for example `http://10.16.103.13:8765/location` and token `4a13fc97`. The token is also in `~/.config/raven-reach/config.json` under `phone_bridge.token`.

In the iPhone **Shortcuts** app, create a shortcut named "Reach GPS":
1. **Repeat** 200 times
2. **Get Current Location**
3. **Get Contents of URL** → the URL above, Method **POST**, Request Body **JSON** with these fields:
   - `token` (Text): your token
   - `lat` (Number): *Current Location → Latitude*
   - `lon` (Number): *Current Location → Longitude*
   - `address` (Text, optional): *Current Location → Street*, *City*
4. **Wait** 15 seconds
5. End Repeat

Run it before presenting. The app header shows `GPS 5s` while fixes arrive. Emergency messages use the phone fix while it's under 2 minutes old ("Phone GPS location, N seconds old…"). After that they fall back to the typed location.
- The phone and the glasses (or Mac) must be on the same Wi-Fi. Venue or guest Wi-Fi often blocks device-to-device traffic, so a phone hotspot is the safer bet.
- macOS asks whether Python may accept incoming connections. Click **Allow**.

### Location
- `location` in config: the address and coordinates you type, read out as "set by the wearer".
- `"ip_location_consent": true` (used only when no manual location is set): an approximate city from your IP via ipapi.co, read out as approximate.
- Nothing else. The app never makes up an address.

## Safety rules kept from v1.7
- Confirm stays locked until every review page has been read. Cancel always works and sends nothing.
- After any selection, buttons re-arm only once your gaze leaves that spot, so one long stare can't chain into Confirm.
- Only facts the wearer selects are spoken. Nothing is inferred and no AI writes the words.
- Losing gaze never hangs up. Only *End call… → Yes, end the call* does.
- "Completed" means the call ended. It does not mean anyone understood the message or that help is coming.

## Files
- `main.py`: entry point
- `reach/ui.py`: gaze UI (Raven SDK)
- `reach/caller.py`: Twilio REST caller and preview caller
- `reach/script.py`: spoken message and TwiML
- `reach/location.py`: manual or IP location
- `reach/safety.py`: number guard
- `tools/smoke.py`: drives the real SDK simulator end to end
- `tools/test_call.py`: one CLI call
- `tests/`: `pytest`
