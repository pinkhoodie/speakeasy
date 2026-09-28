import AppKit
import SpeakeasyCore
import SpeakeasyClient
import SwiftUI

/// Click, then press a key combination. Esc cancels; Delete resets to the default.
/// With `onTurnOff`, a small button turns the shortcut off (shown as "Off").
struct ShortcutRecorder: View {
    var shortcut: KeyShortcut?
    var name: String = "Call"
    var defaultShortcut: KeyShortcut = .call
    /// Other shortcuts this one must not equal.
    var taken: [KeyShortcut] = []
    var onTurnOff: (() -> Void)? = nil
    var onChange: (KeyShortcut) -> Void
    @State private var recording = false
    @State private var problem: String?
    @State private var monitor: Any?

    var body: some View {
        VStack(alignment: .trailing, spacing: 3) {
            HStack(spacing: 6) {
                Button(action: toggle) {
                    Text(recording ? "Press keys…" : (shortcut?.display ?? "Off"))
                        .font(.system(.body, design: .rounded).weight(.medium))
                        .frame(minWidth: 110)
                }
                .help(recording ? "Press a key combination. Esc cancels, Delete restores \(defaultShortcut.display)."
                                : "Click, then press a new combination")
                .accessibilityLabel(recording ? "Recording shortcut. Press a key combination, or Escape to cancel."
                                              : "\(name) shortcut \(shortcut?.spoken ?? "off"). Click to change.")
                if let onTurnOff, shortcut != nil, !recording {
                    Button { onTurnOff() } label: { Image(systemName: "xmark.circle.fill") }
                        .buttonStyle(.borderless).foregroundStyle(.secondary)
                        .help("Turn the \(name.lowercased()) shortcut off")
                        .accessibilityLabel("Turn the \(name.lowercased()) shortcut off")
                }
            }
            if let problem { Text(problem).font(.caption).foregroundStyle(.orange) }
        }
        .onDisappear(perform: stop)
    }

    private func toggle() { recording ? stop() : start() }

    private func start() {
        problem = nil
        recording = true
        monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { event in
            handle(event)
            return nil
        }
    }

    private func stop() {
        recording = false
        if let monitor { NSEvent.removeMonitor(monitor) }
        monitor = nil
    }

    private func handle(_ event: NSEvent) {
        if event.keyCode == 0x35 { stop(); return }                 // Esc
        if event.keyCode == 0x33 { onChange(defaultShortcut); stop(); return } // Delete → default
        var mods: UInt32 = 0
        let flags = event.modifierFlags
        if flags.contains(.control) { mods |= KeyShortcut.control }
        if flags.contains(.option) { mods |= KeyShortcut.option }
        if flags.contains(.shift) { mods |= KeyShortcut.shift }
        if flags.contains(.command) { mods |= KeyShortcut.command }
        switch KeyShortcut.recorded(keyCode: UInt32(event.keyCode), modifiers: mods) {
        case .success(let s):
            if GlobalHotKey.collidesWithSystemShortcut(keyCode: s.keyCode, modifiers: s.modifiers) {
                problem = "\(s.display) is a macOS shortcut"
            } else if taken.contains(s) {
                problem = "\(s.display) is already another Speakeasy shortcut"
            } else {
                onChange(s)
            }
            stop()
        case .failure(let error):
            switch error {
            case .tooFewModifiers: problem = "Use at least two modifiers (e.g. ⌃⌥)"
            case .unknownToken, .missingKey: problem = "That key isn't supported"
            default: problem = "Try another combination"
            }
        }
    }
}
