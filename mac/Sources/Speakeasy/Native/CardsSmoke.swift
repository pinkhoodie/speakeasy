import AppKit
import SwiftUI
import SpeakeasyCore
import SpeakeasyClient

/// `--cards-smoke <cards.json> <dir>`: draws every card in a JSON array (the api's `views` format)
/// with the shared card views, light and dark, at the panel's width, and writes one PNG per card.
/// Prints `cards-smoke ok N` or the kinds that failed to decode. The JSON comes from the live plugin
/// (real prices, weather, games) plus sample cards for the kinds that need personal data.
private extension JSONValue {
    var hasRemoteImage: Bool {
        switch self {
        case .string(let v): return v.hasPrefix("https://")
        case .array(let a): return a.contains { $0.hasRemoteImage }
        case .object(let o): return o.values.contains { $0.hasRemoteImage }
        default: return false
        }
    }
}

enum CardsSmoke {
    @MainActor
    static func run(json path: String, dir: String) {
        guard let data = FileManager.default.contents(atPath: path),
              let raw = try? JSONSerialization.jsonObject(with: data) as? [Any] else {
            print("cards-smoke: could not read \(path)"); exit(2)
        }
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        var drawn = 0
        var skipped: [String] = []
        for (index, item) in raw.enumerated() {
            guard let card = ViewCard(json: item, number: index + 1) else {
                skipped.append(((item as? [String: Any])?["kind"] as? String) ?? "?"); continue
            }
            for scheme in [ColorScheme.light, .dark] {
                // A real window (not ImageRenderer): maps, links, buttons and remote logos only draw there.
                let view = ViewCardView(card)
                    .environment(\.draftActions, DraftActions(onSend: { _ in }, onEdit: { _ in }))
                    .environment(\.questionActions, QuestionActions(onAnswer: { _ in },
                                                                    answered: card["answered"].string))
                    .padding(12)
                    .frame(width: 380)
                    .background(scheme == .dark ? Color(white: 0.13) : Color(white: 0.97))
                    .environment(\.colorScheme, scheme)
                let host = NSHostingView(rootView: view)
                host.appearance = NSAppearance(named: scheme == .dark ? .darkAqua : .aqua)
                let size = host.fittingSize
                host.frame = NSRect(origin: .zero, size: size)
                let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
                window.contentView = host
                window.orderFrontRegardless()
                let slow = card.data.hasRemoteImage || ["place", "route"].contains(card.kind)
                RunLoop.main.run(until: Date().addingTimeInterval(slow ? 3.0 : 0.6))
                host.layoutSubtreeIfNeeded()
                guard let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds) else {
                    skipped.append(card.kind + "(render)"); window.close(); continue
                }
                host.cacheDisplay(in: host.bounds, to: rep)
                window.close()
                guard let png = rep.representation(using: .png, properties: [:]) else { skipped.append(card.kind + "(png)"); continue }
                let name = String(format: "%02d-%@-%@.png", index + 1, card.kind, scheme == .dark ? "dark" : "light")
                try? png.write(to: URL(fileURLWithPath: dir).appendingPathComponent(name))
            }
            drawn += 1
        }
        print(skipped.isEmpty ? "cards-smoke ok \(drawn)" : "cards-smoke drew \(drawn), skipped: \(skipped.joined(separator: ", "))")
        exit(skipped.isEmpty ? 0 : 1)
    }
}
