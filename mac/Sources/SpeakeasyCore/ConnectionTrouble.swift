import Foundation

/// Plain-language reasons a call couldn't reach Hermes. Speakeasy is reachable only on the user's
/// tailnet (an https *.ts.net address), so "host not found" or "can't connect" to such an address
/// almost always means Tailscale is off on this device. Other errors keep their own wording.
public enum ConnectionTrouble {
    public static func isTailnet(_ url: URL?) -> Bool {
        guard let host = url?.host?.lowercased() else { return false }
        return host.hasSuffix(".ts.net")
    }

    /// The URL error codes that mean "this address isn't reachable from here".
    static let unreachable: Set<Int> = [
        NSURLErrorCannotFindHost, NSURLErrorDNSLookupFailed, NSURLErrorCannotConnectToHost,
        NSURLErrorNotConnectedToInternet, NSURLErrorTimedOut, NSURLErrorNetworkConnectionLost,
        NSURLErrorInternationalRoamingOff, NSURLErrorDataNotAllowed,
    ]

    public static func message(for error: Error, server: URL?) -> String {
        let ns = error as NSError
        guard ns.domain == NSURLErrorDomain, unreachable.contains(ns.code) else {
            return error.localizedDescription
        }
        if ns.code == NSURLErrorNotConnectedToInternet || ns.code == NSURLErrorDataNotAllowed {
            return "This device is offline. Connect to Wi-Fi or cellular, then try again."
        }
        guard isTailnet(server) else {
            return "Couldn't reach your Hermes. Check that the Hermes machine is on and awake, then try again."
        }
        #if os(visionOS)
        return "Couldn't reach your Hermes. Tailscale may be off on this Vision Pro: open Tailscale, turn it on, then try again."
        #elseif os(iOS)
        return "Couldn't reach your Hermes. Tailscale may be off on this phone: open Tailscale, turn it on, then try again."
        #else
        return "Couldn't reach your Hermes. Tailscale may be off on this Mac: open Tailscale, connect, then try again."
        #endif
    }

    /// True when the message is the Tailscale hint, so the app can offer an "Open Tailscale" button.
    public static func suggestsTailscale(_ message: String) -> Bool {
        message.contains("Tailscale may be off")
    }
}
