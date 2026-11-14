import CoreImage.CIFilterBuiltins
import MessageUI
import SwiftUI

/// Apple's SMS composer, prefilled; the user taps Send.
struct MessageComposeView: UIViewControllerRepresentable {
    let draft: TextDraft
    let onFinish: (MessageComposeResult) -> Void
    func makeCoordinator() -> Coordinator { Coordinator(onFinish: onFinish) }
    func makeUIViewController(context: Context) -> MFMessageComposeViewController {
        let vc = MFMessageComposeViewController()
        vc.recipients = [draft.to]
        vc.body = draft.body
        vc.messageComposeDelegate = context.coordinator
        return vc
    }
    func updateUIViewController(_ vc: MFMessageComposeViewController, context: Context) {}
    final class Coordinator: NSObject, MFMessageComposeViewControllerDelegate {
        let onFinish: (MessageComposeResult) -> Void
        init(onFinish: @escaping (MessageComposeResult) -> Void) { self.onFinish = onFinish }
        func messageComposeViewController(_ c: MFMessageComposeViewController, didFinishWith result: MessageComposeResult) {
            onFinish(result)
        }
    }
}

@main
struct RavenReachPhoneApp: App {
    @StateObject private var phone = ReachPhone()
    var body: some Scene {
        WindowGroup {
            ContentView().environmentObject(phone)
                .onOpenURL { url in
                    // Shortcuts reporting back after an automatic text, or a glasses QR from the Camera app.
                    if !phone.handleCallback(url) { phone.pair(with: url) }
                }
        }
    }
}

/// Black-on-white QR code for the glasses' camera to scan.
struct QRCodeImage: View {
    let text: String
    var body: some View {
        let filter = CIFilter.qrCodeGenerator()
        filter.message = Data(text.utf8)
        filter.correctionLevel = "M"
        let ctx = CIContext()
        if let out = filter.outputImage?.transformed(by: CGAffineTransform(scaleX: 12, y: 12)),
           let cg = ctx.createCGImage(out, from: out.extent) {
            return AnyView(Image(decorative: cg, scale: 1).interpolation(.none).resizable().scaledToFit()
                .padding(16).background(Color.white).clipShape(RoundedRectangle(cornerRadius: 16)))
        }
        return AnyView(Text("QR unavailable"))
    }
}

struct ContentView: View {
    @EnvironmentObject var phone: ReachPhone
    @Environment(\.scenePhase) private var scenePhase
    @State private var sosInput = ""
    @State private var sosError: String?
    @State private var scanning = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    QRCodeImage(text: phone.pairingQR)
                        .frame(maxWidth: 240).frame(maxWidth: .infinity)
                    Text("On the glasses open **Phone link → Scan phone** and look at this code. This phone then finds the glasses on Wi-Fi by itself.")
                        .font(.callout)
                    Button {
                        scanning = true
                    } label: {
                        Label("Scan QR code on the glasses / Mac", systemImage: "qrcode.viewfinder")
                            .frame(maxWidth: .infinity).padding(.vertical, 6)
                    }
                    .buttonStyle(.borderedProminent)
                    Text("Or let the glasses scan this phone's code above.").font(.footnote).foregroundStyle(.secondary)
                    Label(phone.glassesOnline ? "Glasses connected" : phone.findLine,
                          systemImage: phone.glassesOnline ? "checkmark.circle.fill" : "antenna.radiowaves.left.and.right")
                        .foregroundStyle(phone.glassesOnline ? .green : .secondary)
                } header: { Text("Pair with glasses") }

                Section {
                    Text(phone.callLine).bold()
                    Button {
                        phone.speakNow()
                    } label: {
                        Label("Speak message into call", systemImage: "speaker.wave.3.fill")
                            .frame(maxWidth: .infinity).padding(.vertical, 8)
                    }
                    .buttonStyle(.borderedProminent).tint(.red)
                    .disabled(!phone.hasActiveRequest)
                    if !phone.lastScript.isEmpty {
                        Text(phone.lastScript).font(.footnote).foregroundStyle(.secondary)
                    }
                } header: { Text("Emergency call") } footer: {
                    Text("After tapping Call, switch back to Raven Reach (tap the green call pill, then swipe to this app). It speaks automatically when it's open during the call; use the button to speak again.")
                }

                Section {
                    TextField("Your name", text: $phone.wearerName)
                        .textContentType(.name)
                    TextField("Appearance & info (e.g. red jacket, asthma, inhaler in left pocket)",
                              text: $phone.appearance, axis: .vertical)
                        .lineLimit(2...5)
                    Text(phone.profileLine).font(.footnote).foregroundStyle(.secondary)
                } header: { Text("About you") } footer: {
                    Text("Read out in the emergency message. Only what you write here is said.")
                }

                Section {
                    TextField("+1 314 555 0199", text: $sosInput).keyboardType(.phonePad)
                    Button("Save SOS number") { sosError = phone.saveSOS(sosInput); if sosError == nil { sosInput = phone.sosNumber } }
                    if let sosError { Text(sosError).font(.footnote).foregroundStyle(.red) }
                    Text(phone.sosLine).font(.footnote).foregroundStyle(.secondary)
                } header: { Text("SOS number") } footer: {
                    Text("The phone the glasses' SOS button calls for the demo. Use a phone you own — never 911, 112 or 999.")
                }

                Section {
                    Toggle("Also text the message + map link", isOn: $phone.textAlongside)
                    Toggle("Send it automatically", isOn: $phone.autoText).disabled(!phone.textAlongside)
                    if phone.textAlongside && phone.autoText {
                        DisclosureGroup("One-time setup: create the shortcut") {
                            VStack(alignment: .leading, spacing: 6) {
                                Text("1. Open **Shortcuts** → **+** → name it **\(ReachPhone.shortcutName)**")
                                Text("2. Add action **Send Message**")
                                Text("3. Message: tap it → **Shortcut Input**")
                                Text("4. Recipients: your SOS number (the same phone as above)")
                                Text("5. Tap the ▸ arrow on Send Message → turn **Show When Run** OFF")
                                Text("6. Run it once from Shortcuts and choose **Always Allow** if asked")
                            }.font(.footnote)
                        }
                    }
                } header: { Text("Text with the call") } footer: {
                    Text(phone.autoText
                         ? "The exact message + a map link to your phone's GPS is sent by the shortcut without a prompt, then the call opens. If the shortcut is missing, Messages opens instead and you tap Send."
                         : "Messages opens with the exact message + map link — tap Send, then Call.")
                }
                Section("Contacts") {
                    Toggle("Share my contacts with the glasses", isOn: $phone.shareContacts)
                    Text(phone.contactsLine).font(.footnote).foregroundStyle(.secondary)
                }
                Section("Phone GPS → glasses") { Text(phone.gpsLine).monospacedDigit() }

                if !phone.log.isEmpty {
                    Section("What happened") {
                        ForEach(phone.log, id: \.self) { Text($0).font(.caption.monospaced()) }
                    }
                }

                Section {
                    DisclosureGroup("Manual connection") {
                        TextField("Glasses address, e.g. 10.16.103.13:8765", text: $phone.host)
                            .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                        TextField("Token", text: $phone.token)
                            .textInputAutocapitalization(.never).autocorrectionDisabled()
                        Toggle("Connect", isOn: Binding(get: { phone.running },
                                                        set: { $0 ? phone.start() : phone.stop() }))
                    }
                } footer: {
                    Text("Demo line only — never point this at real emergency services.").foregroundStyle(.red)
                }
            }
            .navigationTitle("Raven Reach")
            .task {
                sosInput = phone.sosNumber
                phone.start()
                #if DEBUG
                // Test hook: same code path as scanning the glasses' QR (simulator can't tap the Open prompt).
                if let s = UserDefaults.standard.string(forKey: "pairURL"), let u = URL(string: s) { phone.pair(with: u) }
                #endif
            }
            .onChange(of: scenePhase) { _, p in if p == .active { phone.becameActive() } }
            .sheet(item: $phone.textDraft, onDismiss: {
                // Swiped away without choosing: treat as not sent, still place the call (no-op if handled).
                phone.textFinished(.cancelled)
            }) { draft in
                MessageComposeView(draft: draft) { phone.textFinished($0) }.ignoresSafeArea()
            }
            .sheet(isPresented: $scanning) {
                QRScannerSheet { code in
                    if let url = URL(string: code) { phone.pair(with: url) }
                }
            }
        }
    }
}
