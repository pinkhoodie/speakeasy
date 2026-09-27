import AppKit
import SwiftUI

/// `Speakeasy --onboarding-snapshot <file.png>`: renders the first onboarding screen offscreen
/// (no window, no server) so the setup copy can be reviewed as an image.
enum OnboardingSnapshot {
    @MainActor
    static func render(to path: String) {
        let flow = OnboardingFlow(app: AppModel.shared)
        let view = OnboardingView(flow: flow).environmentObject(AppModel.shared)
            .frame(width: 520, height: 460).background(Color(nsColor: .windowBackgroundColor))
        let host = NSHostingView(rootView: view)
        host.frame = NSRect(x: 0, y: 0, width: 520, height: 460)
        let window = NSWindow(contentRect: host.frame, styleMask: [.titled], backing: .buffered, defer: false)
        window.appearance = NSAppearance(named: .darkAqua)
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        RunLoop.main.run(until: Date().addingTimeInterval(0.4))
        guard let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds) else { exit(1) }
        host.cacheDisplay(in: host.bounds, to: rep)
        guard let png = rep.representation(using: .png, properties: [:]) else { exit(1) }
        try? png.write(to: URL(fileURLWithPath: path))
        print("snapshot \(path)")
        exit(0)
    }
}
