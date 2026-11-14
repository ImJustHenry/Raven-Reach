import SwiftUI
import VisionKit

/// In-app scanner for the QR code shown on the glasses / Mac (Phone link screen).
/// Uses Apple's DataScannerViewController (iPhone XS / iOS 16 or newer).
struct QRScannerSheet: View {
    let onCode: (String) -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Group {
                if DataScannerViewController.isSupported && DataScannerViewController.isAvailable {
                    QRScannerView { code in
                        onCode(code)
                        dismiss()
                    }
                    .ignoresSafeArea()
                } else {
                    VStack(spacing: 16) {
                        Image(systemName: "camera.fill").font(.largeTitle)
                        Text("The in-app scanner isn't available here (camera access off, or this device/simulator doesn't support it).")
                        Text("Use the iPhone **Camera** app instead: point it at the QR code on the glasses' Phone link screen and tap **Open in Raven Reach**.")
                    }
                    .multilineTextAlignment(.center).padding()
                }
            }
            .navigationTitle("Scan glasses QR")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
        }
    }
}

private struct QRScannerView: UIViewControllerRepresentable {
    let onCode: (String) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(onCode: onCode) }

    func makeUIViewController(context: Context) -> DataScannerViewController {
        let vc = DataScannerViewController(recognizedDataTypes: [.barcode(symbologies: [.qr])],
                                           qualityLevel: .balanced, recognizesMultipleItems: false,
                                           isHighFrameRateTrackingEnabled: false, isHighlightingEnabled: true)
        vc.delegate = context.coordinator
        try? vc.startScanning()
        return vc
    }

    func updateUIViewController(_ vc: DataScannerViewController, context: Context) {
        if !vc.isScanning { try? vc.startScanning() }
    }

    static func dismantleUIViewController(_ vc: DataScannerViewController, coordinator: Coordinator) {
        vc.stopScanning()
    }

    final class Coordinator: NSObject, DataScannerViewControllerDelegate {
        let onCode: (String) -> Void
        private var done = false
        init(onCode: @escaping (String) -> Void) { self.onCode = onCode }

        func dataScanner(_ scanner: DataScannerViewController, didAdd items: [RecognizedItem], allItems: [RecognizedItem]) {
            for item in items {
                // Only accept Raven Reach pairing codes; ignore any other QR in view.
                if case .barcode(let b) = item, let s = b.payloadStringValue,
                   s.hasPrefix("ravenreach://pair?"), !done {
                    done = true
                    scanner.stopScanning()
                    onCode(s)
                    return
                }
            }
        }
    }
}
