import CryptoKit
import Foundation

/// Finds the paired glasses on the local network (Bonjour, _ravenreach._tcp).
/// The glasses announce only a short hash of the pairing token ("id"), never the token.
final class GlassesFinder: NSObject, NetServiceBrowserDelegate, NetServiceDelegate {
    private let browser = NetServiceBrowser()
    private var pending: [NetService] = []
    private var wantedID = ""
    private var onFound: ((String) -> Void)?
    private(set) var searching = false

    static func pairID(_ token: String) -> String {
        let digest = SHA256.hash(data: Data(("raven-reach:" + token).utf8))
        return digest.map { String(format: "%02x", $0) }.joined().prefix(12).lowercased()
    }

    /// Calls `found("ip:port")` on the main queue when glasses with our pairing id appear.
    func start(token: String, found: @escaping (String) -> Void) {
        stop()
        wantedID = Self.pairID(token)
        onFound = found
        searching = true
        browser.delegate = self
        browser.searchForServices(ofType: "_ravenreach._tcp.", inDomain: "local.")
    }

    func stop() {
        guard searching else { return }
        searching = false
        browser.stop()
        pending.forEach { $0.stop() }
        pending.removeAll()
    }

    func netServiceBrowser(_ b: NetServiceBrowser, didFind service: NetService, moreComing: Bool) {
        pending.append(service)
        service.delegate = self
        service.resolve(withTimeout: 5)
    }

    func netServiceDidResolveAddress(_ sender: NetService) {
        guard let txt = sender.txtRecordData(),
              let id = NetService.dictionary(fromTXTRecord: txt)["id"].flatMap({ String(data: $0, encoding: .utf8) }),
              id == wantedID, let ip = Self.ipv4(sender.addresses ?? []) else { return }
        let hostPort = "\(ip):\(sender.port)"
        DispatchQueue.main.async { self.onFound?(hostPort) }
    }

    private static func ipv4(_ addresses: [Data]) -> String? {
        for data in addresses {
            let ip: String? = data.withUnsafeBytes { raw in
                guard raw.count >= MemoryLayout<sockaddr_in>.size else { return nil }
                let sa = raw.load(as: sockaddr_in.self)
                guard sa.sin_family == sa_family_t(AF_INET) else { return nil }
                var addr = sa.sin_addr
                var buf = [CChar](repeating: 0, count: Int(INET_ADDRSTRLEN))
                return inet_ntop(AF_INET, &addr, &buf, socklen_t(INET_ADDRSTRLEN)).map { String(cString: $0) }
            }
            if let ip { return ip }
        }
        return nil
    }
}
