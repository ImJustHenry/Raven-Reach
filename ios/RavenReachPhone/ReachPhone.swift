import AVFoundation
import CallKit
import Contacts
import CoreLocation
import Foundation
import MessageUI
import UIKit

/// One emergency request confirmed on the glasses. The script is the exact text the
/// wearer reviewed; the phone never rewrites it.
struct ReachRequest: Decodable, Equatable {
    let id: String
    let number: String
    let script: String
    let age: Double
    let map: String?
}

/// A text the user confirms in Apple's composer (iOS never lets apps send SMS silently).
struct TextDraft: Identifiable {
    let id: String
    let to: String
    let body: String
}

private struct RequestReply: Decodable { let request: ReachRequest? }

/// Talks to the Raven Reach app on the glasses over local Wi-Fi:
///  - sends this phone's GPS (POST /location)
///  - polls for a confirmed emergency request (GET /request)
///  - opens the call, then speaks the script INTO the connected call with
///    AVSpeechSynthesizer.mixToTelephonyUplink (Apple's assistive speech route)
///  - reports progress back (POST /request/status)
@MainActor
final class ReachPhone: NSObject, ObservableObject {
    @Published var host: String { didSet { UserDefaults.standard.set(host, forKey: "host") } }
    @Published var token: String { didSet { UserDefaults.standard.set(token, forKey: "token") } }
    @Published var glassesOnline = false
    @Published var gpsLine = "No GPS yet"
    @Published var callLine = "Waiting for a request from the glasses"
    @Published var lastScript = ""
    @Published var running = false
    @Published var sosNumber: String { didSet { UserDefaults.standard.set(sosNumber, forKey: "sos"); settingsVersion += 1 } }
    @Published var wearerName: String { didSet { UserDefaults.standard.set(wearerName, forKey: "name"); settingsVersion += 1 } }
    @Published var appearance: String { didSet { UserDefaults.standard.set(appearance, forKey: "appearance"); settingsVersion += 1 } }
    @Published var profileLine = "Not sent yet"
    @Published var findLine = "Not paired yet"
    @Published var log: [String] = []
    @Published var hasActiveRequest = false
    @Published var textDraft: TextDraft?
    private var openDraft: TextDraft?   // survives the sheet binding being cleared on swipe-down
    @Published var textAlongside: Bool { didSet { UserDefaults.standard.set(textAlongside, forKey: "sms") } }
    /// Send automatically through the "Raven Reach Text" shortcut instead of the Send sheet.
    @Published var autoText: Bool { didSet { UserDefaults.standard.set(autoText, forKey: "autoText") } }
    static let shortcutName = "Raven Reach Text"
    private var autoTextTimer: DispatchWorkItem?
    @Published var sosLine = "Using the glasses' config"
    @Published var shareContacts: Bool { didSet { UserDefaults.standard.set(shareContacts, forKey: "shareContacts"); lastContactSync = .distantPast } }
    @Published var contactsLine = "Contacts not shared"

    private let locations = CLLocationManager()
    private let geocoder = CLGeocoder()
    private let calls = CXCallObserver()
    private let synth = AVSpeechSynthesizer()
    private var pollTask: Task<Void, Never>?
    private var lastFix: CLLocation?
    private var lastSent = Date.distantPast
    private var address = ""
    private var lastGeocoded: CLLocation?
    private var handled = Set<String>()
    private var active: ReachRequest?
    private var connected = false
    private var spokeFor: String?
    private var bgTask: UIBackgroundTaskIdentifier = .invalid
    private var settingsVersion = 1
    private var sentVersion = 0
    private var lastSOSSync = Date.distantPast
    private let finder = GlassesFinder()
    private var offlineSince: Date?
    private var speechStartedFor: String?
    private var lastContactSync = Date.distantPast
    private var syncingContacts = false

    override init() {
        host = UserDefaults.standard.string(forKey: "host") ?? ""
        let saved = UserDefaults.standard.string(forKey: "token") ?? ""
        token = saved.count >= 6 ? saved : ReachPhone.newToken()
        sosNumber = UserDefaults.standard.string(forKey: "sos") ?? ""
        wearerName = UserDefaults.standard.string(forKey: "name") ?? ""
        appearance = UserDefaults.standard.string(forKey: "appearance") ?? ""
        shareContacts = UserDefaults.standard.object(forKey: "shareContacts") as? Bool ?? true
        textAlongside = UserDefaults.standard.object(forKey: "sms") as? Bool ?? true
        autoText = UserDefaults.standard.object(forKey: "autoText") as? Bool ?? true
        super.init()
        locations.delegate = self
        locations.desiredAccuracy = kCLLocationAccuracyBest
        locations.distanceFilter = 5
        calls.setDelegate(self, queue: .main)
        synth.delegate = self
        // Apple's documented assistive route (WWDC20 "Create a seamless speech experience"):
        // during an active call, synthesized speech is mixed into the call's uplink.
        synth.mixToTelephonyUplink = true
        synth.usesApplicationAudioSession = false
    }

    // MARK: phone shows QR, glasses scan it
    /// What this phone's QR code encodes. The glasses adopt the token, then announce
    /// themselves on Wi-Fi so this phone finds them (no IP address to type).
    var pairingQR: String {
        let name = UIDevice.current.name.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? "iPhone"
        return "ravenreach://phone?t=\(token)&n=\(name)"
    }

    static func newToken() -> String {
        (0..<10).map { _ in String("abcdefghjkmnpqrstuvwxyz23456789".randomElement()!) }.joined()
    }

    func note(_ line: String) {
        let t = DateFormatter.localizedString(from: Date(), dateStyle: .none, timeStyle: .medium)
        log.insert("\(t)  \(line)", at: 0)
        if log.count > 12 { log.removeLast() }
    }

    // MARK: glasses-QR pairing (Camera app opens ravenreach://pair?...)
    /// Handles ravenreach://pair?h=<ip:port>&t=<token> from the QR code on the glasses.
    @discardableResult
    func pair(with url: URL) -> Bool {
        guard url.scheme == "ravenreach", url.host == "pair",
              let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems,
              let h = items.first(where: { $0.name == "h" })?.value,
              let t = items.first(where: { $0.name == "t" })?.value,
              t.count >= 6, h.range(of: #"^[A-Za-z0-9.\-]+:[0-9]{1,5}$"#, options: .regularExpression) != nil
        else { callLine = "That QR code isn't a Raven Reach pairing code"; return false }
        stop()
        host = h; token = t
        sentVersion = 0; lastContactSync = .distantPast
        callLine = "Paired with glasses at \(h)"
        start()
        return true
    }

    // MARK: lifecycle
    func start() {
        guard !running else { return }
        running = true
        locations.requestWhenInUseAuthorization()
        locations.allowsBackgroundLocationUpdates = true   // keeps us alive during the call
        locations.showsBackgroundLocationIndicator = true
        locations.startUpdatingLocation()
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.pollOnce()
                try? await Task.sleep(nanoseconds: 1_000_000_000)
            }
        }
    }

    func stop() {
        running = false
        pollTask?.cancel(); pollTask = nil
        locations.stopUpdatingLocation()
        glassesOnline = false
    }

    // MARK: networking
    private var base: URL? {
        let h = host.trimmingCharacters(in: .whitespaces)
        guard !h.isEmpty else { return nil }
        return URL(string: h.hasPrefix("http") ? h : "http://\(h.contains(":") ? h : h + ":8765")")
    }

    private func pollOnce() async {
        guard let base else { glassesOnline = false; discoverIfNeeded(); return }
        var req = URLRequest(url: base.appendingPathComponent("request"), timeoutInterval: 3)
        req.setValue(token, forHTTPHeaderField: "X-Reach-Token")
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            glassesOnline = code == 200
            if code == 403 { callLine = "Glasses rejected the token. Check it in Setup." }
            if code == 200, let r = try JSONDecoder().decode(RequestReply.self, from: data).request {
                handle(r)
            }
        } catch {
            glassesOnline = false
        }
        if let fix = lastFix, Date().timeIntervalSince(lastSent) > 5 { await send(fix) }
        if glassesOnline {
            offlineSince = nil
            if finder.searching { finder.stop() }
            findLine = "Connected to glasses at \(host)"
            await syncSettings(); syncContactsIfDue()
        } else {
            discoverIfNeeded()
        }
    }

    private func discoverIfNeeded() {
        do {
            sentVersion = 0
            if offlineSince == nil { offlineSince = Date() }
            // Look for our glasses on Wi-Fi (they announce a hash of our token).
            if !finder.searching, host.isEmpty || Date().timeIntervalSince(offlineSince!) > 2, token.count >= 6 {
                findLine = "Looking for your glasses on Wi-Fi…"
                finder.start(token: token) { [weak self] found in
                    guard let self else { return }
                    self.host = found
                    self.findLine = "Found glasses at \(found)"
                    self.note("Found glasses on Wi-Fi at \(found)")
                    self.finder.stop()
                }
            }
        }
    }

    // MARK: SOS number + contacts
    /// Typed SOS number -> +E.164, or nil. Never an emergency / short number.
    static func normalizeSOS(_ typed: String) -> String? {
        let t = typed.trimmingCharacters(in: .whitespaces)
        let digits = t.filter(\.isNumber)
        let n: String
        if t.hasPrefix("+") { n = "+" + digits }
        else if digits.count == 10 { n = "+1" + digits }
        else if digits.count == 11, digits.hasPrefix("1") { n = "+" + digits }
        else { return nil }
        return isAllowedTestNumber(n) ? n : nil
    }

    /// Returns an error message, or nil when saved.
    func saveSOS(_ typed: String) -> String? {
        if typed.trimmingCharacters(in: .whitespaces).isEmpty { sosNumber = ""; sosLine = "Using the glasses' config"; return nil }
        guard let n = Self.normalizeSOS(typed) else {
            return "Use an ordinary number like +1 314 555 0199. Emergency numbers (911, 112, 999…) are refused."
        }
        sosNumber = n
        sosLine = "Saved •••\(n.suffix(4)) — sending to glasses…"
        return nil
    }

    private func syncSettings() async {
        guard let base, settingsVersion != sentVersion || Date().timeIntervalSince(lastSOSSync) > 120 else { return }
        lastSOSSync = Date()
        let version = settingsVersion
        var req = URLRequest(url: base.appendingPathComponent("settings"), timeoutInterval: 3)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue(token, forHTTPHeaderField: "X-Reach-Token")
        req.httpBody = try? JSONSerialization.data(withJSONObject: [
            "sos_number": sosNumber, "wearer_name": wearerName, "description": appearance])
        guard let (data, resp) = try? await URLSession.shared.data(for: req) else { return }
        if (resp as? HTTPURLResponse)?.statusCode == 200 {
            sentVersion = version
            if !sosNumber.isEmpty { sosLine = "•••\(sosNumber.suffix(4)) set on glasses" }
            profileLine = wearerName.isEmpty && appearance.isEmpty ? "Using the glasses' config"
                : "Sent to glasses — used in the emergency message"
        } else {
            let err = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["error"] as? String
            sosLine = "Glasses refused it: \(err ?? "error")"
        }
    }

    private func syncContactsIfDue() {
        guard shareContacts, !syncingContacts, Date().timeIntervalSince(lastContactSync) > 120,
              let base else { if !shareContacts { contactsLine = "Contacts not shared" }; return }
        lastContactSync = Date()
        syncingContacts = true
        let token = self.token
        let region = Locale.current.region?.identifier ?? "US"
        CNContactStore().requestAccess(for: .contacts) { granted, _ in
            guard granted else {
                Task { @MainActor in self.contactsLine = "Contacts permission denied (Settings → Raven Reach)"; self.syncingContacts = false }
                return
            }
            // Contacts enumeration is synchronous; keep it off the main thread.
            DispatchQueue.global(qos: .utility).async {
                let list = ReachPhone.readContacts(region: region)
                var req = URLRequest(url: base.appendingPathComponent("contacts"), timeoutInterval: 10)
                req.httpMethod = "POST"
                req.setValue("application/json", forHTTPHeaderField: "Content-Type")
                req.setValue(token, forHTTPHeaderField: "X-Reach-Token")
                req.httpBody = try? JSONSerialization.data(withJSONObject: ["contacts": list])
                URLSession.shared.dataTask(with: req) { data, resp, _ in
                    let ok = (resp as? HTTPURLResponse)?.statusCode == 200
                    let n = (data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] })?["accepted"] as? Int
                    Task { @MainActor in
                        self.contactsLine = ok ? "Shared \(n ?? list.count) contacts with glasses" : "Contact sync failed"
                        self.syncingContacts = false
                    }
                }.resume()
            }
        }
    }

    private static let callingCodes = ["US": "1", "CA": "1", "GB": "44", "IN": "91", "AU": "61", "DE": "49",
                                       "FR": "33", "CN": "86", "JP": "81", "KR": "82", "MX": "52", "BR": "55"]

    /// Address-book number -> +E.164 or nil if we can't be sure.
    nonisolated static func e164(_ raw: String, region: String) -> String? {
        let digits = raw.filter(\.isNumber)
        let cc = callingCodes[region] ?? ""
        let n: String
        if raw.trimmingCharacters(in: .whitespaces).hasPrefix("+") { n = "+" + digits }
        else if digits.hasPrefix("00") { n = "+" + digits.dropFirst(2) }
        else if cc == "1", digits.count == 11, digits.hasPrefix("1") { n = "+" + digits }
        else if !cc.isEmpty, (7...12).contains(digits.count) {
            n = "+" + cc + String(digits.drop(while: { $0 == "0" }))
        } else { return nil }
        return isAllowedTestNumber(n) ? n : nil
    }

    /// Names + one number per person (mobile/iPhone preferred). iOS doesn't expose Favorites.
    nonisolated static func readContacts(region: String) -> [[String: Any]] {
        let keys: [CNKeyDescriptor] = [CNContactFormatter.descriptorForRequiredKeys(for: .fullName),
                                       CNContactPhoneNumbersKey as CNKeyDescriptor]
        var out: [[String: Any]] = []
        let preferred: Set<String> = [CNLabelPhoneNumberMobile, CNLabelPhoneNumberiPhone]
        try? CNContactStore().enumerateContacts(with: CNContactFetchRequest(keysToFetch: keys)) { c, stop in
            guard let name = CNContactFormatter.string(from: c, style: .fullName), !name.isEmpty else { return }
            let numbers = c.phoneNumbers.sorted { a, _ in preferred.contains(a.label ?? "") }
            guard let number = numbers.lazy.compactMap({ e164($0.value.stringValue, region: region) }).first else { return }
            out.append(["name": name, "number": number, "starred": false])
            if out.count >= 1000 { stop.pointee = true }
        }
        return out
    }

    private func send(_ fix: CLLocation) async {
        guard let base else { return }
        lastSent = Date()
        var body: [String: Any] = ["token": token, "lat": fix.coordinate.latitude,
                                   "lon": fix.coordinate.longitude]
        if fix.horizontalAccuracy >= 0 { body["accuracy"] = fix.horizontalAccuracy }
        if !address.isEmpty { body["address"] = address }
        await post(base.appendingPathComponent("location"), body)
    }

    private func report(_ id: String, _ status: String) {
        guard let base else { return }
        let body: [String: Any] = ["token": token, "id": id, "status": status]
        Task { await post(base.appendingPathComponent("request/status"), body) }
    }

    private func post(_ url: URL, _ body: [String: Any]) async {
        var req = URLRequest(url: url, timeoutInterval: 3)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        _ = try? await URLSession.shared.data(for: req)
    }

    // MARK: emergency request
    nonisolated static func isAllowedTestNumber(_ n: String) -> Bool {
        let blocked: Set<String> = ["911", "112", "999", "000", "110", "119", "118", "100", "101",
                                    "102", "108", "120", "122", "190", "192", "193", "113", "115"]
        let digits = n.filter(\.isNumber)
        guard n.hasPrefix("+"), (8...15).contains(digits.count), !blocked.contains(digits) else { return false }
        return true
    }

    private func handle(_ r: ReachRequest) {
        guard !handled.contains(r.id) else { return }       // never start a second call
        handled.insert(r.id)
        report(r.id, "phone_received")
        guard r.age < 60, Self.isAllowedTestNumber(r.number) else {
            callLine = "Refused request (expired or not an ordinary test number)"
            report(r.id, "failed"); return
        }
        active = r; connected = false; spokeFor = nil; hasActiveRequest = true
        lastScript = r.script
        // Text first (location in writing even before they answer), then the call.
        var body = "[Raven Reach] " + r.script
        if let map = r.map, !map.isEmpty { body += "\nMap: " + map }
        if textAlongside && autoText, let url = Self.shortcutURL(body: body, id: r.id) {
            // Shortcuts' "Send Message" (Show When Run off) sends without a prompt, then
            // x-success jumps back here (ravenreach://texted) and we open the call.
            callLine = "Sending the text automatically…"
            note("Request received — sending the text via the \(Self.shortcutName) shortcut")
            openDraft = TextDraft(id: r.id, to: r.number, body: body)
            let fallback = DispatchWorkItem { [weak self] in
                guard let self, self.openDraft?.id == r.id else { return }
                self.note("Shortcut didn't report back — opening the call anyway")
                self.openDraft = nil
                self.report(r.id, "sms_failed")
                self.placeCall(r)
            }
            autoTextTimer = fallback
            DispatchQueue.main.asyncAfter(deadline: .now() + 25, execute: fallback)
            UIApplication.shared.open(url) { [weak self] ok in
                Task { @MainActor in if !ok { self?.autoTextReturned(id: r.id, success: false) } }
            }
        } else if textAlongside && MFMessageComposeViewController.canSendText() {
            callLine = "Emergency request — tap Send, then Call"
            note("Request received — tap Send on the text, then Call")
            let d = TextDraft(id: r.id, to: r.number, body: body)
            openDraft = d
            textDraft = d
        } else {
            if textAlongside { note("This device can't send texts — calling only"); report(r.id, "sms_failed") }
            placeCall(r)
        }
    }

    static func shortcutURL(body: String, id: String) -> URL? {
        var c = URLComponents(string: "shortcuts://x-callback-url/run-shortcut")!
        c.queryItems = [
            .init(name: "name", value: shortcutName),
            .init(name: "input", value: "text"),
            .init(name: "text", value: body),
            .init(name: "x-success", value: "ravenreach://texted?id=\(id)"),
            .init(name: "x-error", value: "ravenreach://textfail?id=\(id)"),
            .init(name: "x-cancel", value: "ravenreach://textfail?id=\(id)"),
        ]
        return c.url
    }

    /// ravenreach://texted?id=… / textfail?id=… from the Shortcuts app.
    func handleCallback(_ url: URL) -> Bool {
        guard url.scheme == "ravenreach", url.host == "texted" || url.host == "textfail" else { return false }
        let id = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == "id" }?.value ?? ""
        autoTextReturned(id: id, success: url.host == "texted")
        return true
    }

    private func autoTextReturned(id: String, success: Bool) {
        guard let d = openDraft, d.id == id else { return }
        autoTextTimer?.cancel(); autoTextTimer = nil
        if success {
            openDraft = nil
            report(id, "sms_submitted")
            note("Text sent automatically")
            if let r = active, r.id == id { placeCall(r) }
        } else if MFMessageComposeViewController.canSendText() {
            // Shortcut missing or failed: fall back to the Send sheet, then call.
            note("\(Self.shortcutName) shortcut not found or failed — tap Send instead")
            callLine = "Tap Send, then Call"
            textDraft = d
        } else {
            openDraft = nil
            report(id, "sms_failed")
            if let r = active, r.id == id { placeCall(r) }
        }
    }

    /// Called when the message composer closes (sent, cancelled or failed).
    func textFinished(_ result: MessageComposeResult) {
        guard let d = openDraft else { return }   // already handled
        openDraft = nil
        textDraft = nil
        report(d.id, result == .sent ? "sms_submitted" : "sms_failed")
        note(result == .sent ? "Text handed to Messages" : "Text not sent")
        if let r = active, r.id == d.id { placeCall(r) }
    }

    private func placeCall(_ r: ReachRequest) {
        guard let url = URL(string: "tel://\(r.number)") else { report(r.id, "failed"); finish(); return }
        callLine = "Tap Call, then come back to Raven Reach"
        note("Opening the call — tap Call, then come back to Raven Reach")
        beginBackground()
        UIApplication.shared.open(url) { [weak self] ok in
            Task { @MainActor in
                guard let self else { return }
                if ok { self.report(r.id, "call_prompt") }
                else { self.callLine = "Could not open the call"; self.report(r.id, "failed"); self.finish() }
            }
        }
    }

    private func callsChanged() {
        guard let r = active else { return }
        let outgoing = calls.calls.filter { $0.isOutgoing }
        if !connected, outgoing.contains(where: { $0.hasConnected && !$0.hasEnded }) {
            connected = true
            callLine = "Call connected — speaking into the call"
            note("Call connected (app \(UIApplication.shared.applicationState == .active ? "open" : "in background"))")
            report(r.id, "call_connected")
            // Give the person who answered a moment to say hello.
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in self?.speak(r) }
        }
        let live = outgoing.contains { !$0.hasEnded }
        if !live, !outgoing.isEmpty || connected {
            synth.stopSpeaking(at: .immediate)
            report(r.id, connected ? "call_ended" : "not_connected")
            callLine = connected ? "Call ended" : "Call was not connected"
            finish()
        }
    }

    /// The big button / returning to the app: speak now if a call is connected.
    var canSpeakNow: Bool {
        active != nil && calls.calls.contains { $0.isOutgoing && $0.hasConnected && !$0.hasEnded }
    }

    func speakNow() {
        guard let r = active else { note("No active emergency request"); return }
        guard canSpeakNow else { note("No connected call yet — wait for the other phone to answer"); return }
        connected = true
        synth.stopSpeaking(at: .immediate)
        spokeFor = nil
        speak(r)
    }

    /// Called when the app comes back to the foreground during a call.
    func becameActive() {
        callsChanged()
        if let r = active, canSpeakNow, speechStartedFor != r.id {
            note("Back in the app during the call — speaking now")
            speakNow()
        }
    }

    private func speak(_ r: ReachRequest) {
        guard active == r, connected, spokeFor != r.id else { return }
        spokeFor = r.id
        note("Speaking into the call (app \(UIApplication.shared.applicationState == .active ? "open" : "in background — if silent, open Raven Reach"))")
        for (i, text) in [r.script, "Repeating.", r.script, "End of message."].enumerated() {
            let u = AVSpeechUtterance(string: text)
            u.voice = AVSpeechSynthesisVoice(language: "en-US")
            u.rate = AVSpeechUtteranceDefaultSpeechRate * 0.9
            u.postUtteranceDelay = i == 1 ? 0 : 0.8
            synth.speak(u)
        }
        report(r.id, "speaking")
    }

    private func finish() {
        active = nil
        hasActiveRequest = false
        endBackground()
    }

    private func beginBackground() {
        endBackground()
        bgTask = UIApplication.shared.beginBackgroundTask(withName: "reach-call") { [weak self] in
            Task { @MainActor in self?.endBackground() }
        }
    }

    private func endBackground() {
        if bgTask != .invalid { UIApplication.shared.endBackgroundTask(bgTask); bgTask = .invalid }
    }

    // MARK: GPS
    fileprivate func gotFix(_ fix: CLLocation) {
        lastFix = fix
        let acc = fix.horizontalAccuracy >= 0 ? String(format: " ±%.0f m", fix.horizontalAccuracy) : ""
        gpsLine = String(format: "%.5f, %.5f", fix.coordinate.latitude, fix.coordinate.longitude) + acc
        if lastGeocoded == nil || fix.distance(from: lastGeocoded!) > 50 {
            lastGeocoded = fix
            geocoder.reverseGeocodeLocation(fix) { [weak self] marks, _ in
                guard let m = marks?.first else { return }
                let parts = [m.subThoroughfare.map { "\($0) \(m.thoroughfare ?? "")" } ?? m.thoroughfare,
                             m.locality, m.administrativeArea].compactMap { $0 }
                Task { @MainActor in self?.address = parts.isEmpty ? "" : "near " + parts.joined(separator: ", ") }
            }
        }
    }
}

extension ReachPhone: CLLocationManagerDelegate {
    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let fix = locations.last else { return }
        Task { @MainActor in self.gotFix(fix) }
    }
    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        Task { @MainActor in self.gpsLine = "GPS unavailable — check Location permission" }
    }
}

extension ReachPhone: CXCallObserverDelegate {
    nonisolated func callObserver(_ callObserver: CXCallObserver, callChanged call: CXCall) {
        Task { @MainActor in self.callsChanged() }
    }
}

extension ReachPhone: AVSpeechSynthesizerDelegate {
    nonisolated func speechSynthesizer(_ s: AVSpeechSynthesizer, didStart u: AVSpeechUtterance) {
        Task { @MainActor in
            guard let r = self.active, self.speechStartedFor != r.id else { return }
            self.speechStartedFor = r.id
            self.note("Speech started")
        }
    }
    nonisolated func speechSynthesizer(_ s: AVSpeechSynthesizer, didFinish u: AVSpeechUtterance) {
        Task { @MainActor in
            guard u.speechString == "End of message.", let r = self.active else { return }
            self.report(r.id, "spoken")
            self.note("Speech finished — ask the other person what they heard")
            self.callLine = "Message spoken into the call (ask the listener to confirm they heard it)"
        }
    }
}
