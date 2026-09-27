import AppKit
import AVFoundation
import Combine
import SpeakeasyCore
import SwiftUI

@main
struct SpeakeasyApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    var body: some Scene {
        Settings {
            SettingsView()
                .environmentObject(AppModel.shared)
        }
    }
}

/// Menu bar item, global hotkeys, the call client, pairing links and onboarding.
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private let app = AppModel.shared
    private var statusItem: NSStatusItem!
    private var hotKey: GlobalHotKey?
    /// In-call mute shortcut: registered only while a call is open.
    private var muteHotKey: GlobalHotKey?
    private var muteGesture = MuteKeyGesture()
    private var muteShortcut: KeyShortcut?
    private var muteShortcutProblem: String?
    /// Pause/Resume shortcut: registered while a call is open or paused.
    private var pauseHotKey: GlobalHotKey?
    private var pauseShortcut: KeyShortcut?
    private var pauseShortcutProblem: String?
    private var callItem: NSMenuItem?
    private var muteItem: NSMenuItem?
    private var pauseItem: NSMenuItem?
    private var statusLine: NSMenuItem?
    private(set) var native: NativeVoiceClient!
    private var active = false
    private var quitPending = false
    private var idle: IdleContinuity?
    private var onboarding: OnboardingWindowController?
    private var bag = Set<AnyCancellable>()
    /// A pairing link that arrived before launch finished.
    private var pendingLink: URL?

    func applicationWillFinishLaunching(_ notification: Notification) {
        // speakeasy://pair links (Info.plist CFBundleURLTypes). An Apple Event handler works
        // whether or not SwiftUI has a scene that could claim the URL.
        NSAppleEventManager.shared().setEventHandler(self, andSelector: #selector(handleURLEvent(_:reply:)),
                                                     forEventClass: AEEventClass(kInternetEventClass),
                                                     andEventID: AEEventID(kAEGetURL))
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        let args = ProcessInfo.processInfo.arguments
        if args.contains("--native-offer-smoke") { NativeSmoke.offer(); return }
        if args.contains("--native-mic-smoke") { NativeSmoke.mic(); return }
        if args.contains("--live-call-smoke") { LiveCallSmoke.run(); return }
        if args.contains("--panel-smoke") {
            let dir = args.firstIndex(of: "--snapshot-dir").flatMap { $0 + 1 < args.count ? args[$0 + 1] : nil }
            PanelSmoke.run(config: app.config, snapshotDir: dir); return
        }
        if let index = args.firstIndex(of: "--ui-preview") { runPreview(args, index: index); return }

        native = NativeVoiceClient(config: app.config)
        applyClientPrefs()
        resolveMuteShortcut()
        resolvePauseShortcut()
        configureClient()
        configureStatusItem()
        configureIdle()
        registerCallHotKey(app.callShortcut)
        observeModel()

        if let link = pendingLink { pendingLink = nil; handlePairURL(link) }
        else if !app.isPaired || !UserDefaults.standard.bool(forKey: Prefs.onboardingDone) { showOnboarding() }
        Task { await app.refresh() }
        DispatchQueue.main.asyncAfter(deadline: .now() + .milliseconds(400)) { [weak self] in
            guard let self, !self.active else { return }
            self.native.hideIfIdle()
        }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { false }

    func applicationWillTerminate(_ notification: Notification) {
        if active { native.end() }
    }

    // MARK: Model wiring

    private func observeModel() {
        app.configChanged.sink { [weak self] config in
            guard let self else { return }
            if self.active { self.native.end() }
            self.native.update(config: config)
            self.configureIdle()
            self.updateMenu()
        }.store(in: &bag)
        app.settingsChanged.sink { [weak self] settings in
            self?.native.apply(settings: settings)
        }.store(in: &bag)
        app.shortcutChanged.sink { [weak self] shortcut in
            self?.registerCallHotKey(shortcut)
        }.store(in: &bag)
        app.startCall = { [weak self] in
            guard let self, !self.active else { return }
            self.startConversation()
        }
        NotificationCenter.default.publisher(for: UserDefaults.didChangeNotification)
            .receive(on: RunLoop.main)
            .sink { [weak self] _ in self?.applyClientPrefs() }
            .store(in: &bag)
    }

    private func applyClientPrefs() {
        let defaults = UserDefaults.standard
        native?.startMuted = defaults.bool(forKey: Prefs.startMuted)
        native?.showPanelOnStart = defaults.bool(forKey: Prefs.showPanelOnStart)
        native?.followSystemAudio = defaults.bool(forKey: Prefs.followSystemAudio)
        native?.startSlim = defaults.bool(forKey: Prefs.startSlim)
    }

    private func configureIdle() {
        let continuity = IdleContinuity(config: app.config)
        continuity.onOpenWork = { [weak self] in self?.showRecentWork() }
        continuity.onResume = { [weak self] in
            guard let self, self.native.isPaused else { self?.showRecentWork(); return }
            self.native.togglePause()
        }
        continuity.onBadge = { [weak self] on in self?.setBadge(on) }
        idle = continuity
    }

    private func setBadge(_ on: Bool) {
        statusItem?.button?.image = NSImage(systemSymbolName: on ? "waveform.badge.exclamationmark" : "waveform",
                                            accessibilityDescription: "Speakeasy")
    }

    // MARK: Pairing links

    @objc private func handleURLEvent(_ event: NSAppleEventDescriptor, reply: NSAppleEventDescriptor) {
        guard let raw = event.paramDescriptor(forKeyword: keyDirectObject)?.stringValue, let url = URL(string: raw) else { return }
        if native == nil { pendingLink = url; return }
        handlePairURL(url)
    }

    private func handlePairURL(_ url: URL) {
        switch PairingLink.parse(url) {
        case .success(let link):
            showOnboarding()
            onboarding?.flow.pairFromLink(link)
        case .failure(let error):
            let alert = NSAlert()
            alert.messageText = "Couldn't use that pairing link"
            alert.informativeText = error.userMessage
            NSApp.activate(ignoringOtherApps: true)
            alert.runModal()
        }
    }

    // MARK: Onboarding & settings

    func showOnboarding() {
        if onboarding == nil {
            let controller = OnboardingWindowController(app: app)
            controller.onFinish = { [weak self] in
                UserDefaults.standard.set(true, forKey: Prefs.onboardingDone)
                self?.onboarding = nil
            }
            onboarding = controller
        }
        onboarding?.show()
    }

    @objc func openSettings() {
        NSApp.activate(ignoringOtherApps: true)
        NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil)
    }

    // MARK: Menu bar

    private func configureStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.image = NSImage(systemSymbolName: "waveform", accessibilityDescription: "Speakeasy")
        statusItem.button?.toolTip = "Speakeasy"
        let menu = NSMenu()
        let status = NSMenuItem(title: "", action: nil, keyEquivalent: "")
        status.isEnabled = false
        menu.addItem(status); statusLine = status
        menu.addItem(.separator())
        let call = NSMenuItem(title: "Start call", action: #selector(toggleConversation), keyEquivalent: "")
        call.target = self
        menu.addItem(call); callItem = call
        let pause = NSMenuItem(title: "Pause call", action: #selector(togglePauseFromMenu), keyEquivalent: "")
        pause.target = self
        menu.addItem(pause); pauseItem = pause
        let mute = NSMenuItem(title: "Mute microphone", action: #selector(toggleMuteFromMenu), keyEquivalent: "")
        mute.target = self
        menu.addItem(mute); muteItem = mute
        let work = NSMenuItem(title: "Recent work", action: #selector(showRecentWork), keyEquivalent: "")
        work.target = self
        menu.addItem(work)
        let reset = NSMenuItem(title: "Reset panel position and size", action: #selector(resetPanelPosition), keyEquivalent: "")
        reset.target = self
        menu.addItem(reset)
        menu.addItem(.separator())
        let settings = NSMenuItem(title: "Settings…", action: #selector(openSettings), keyEquivalent: ",")
        settings.target = self
        menu.addItem(settings)
        let quit = NSMenuItem(title: "Quit Speakeasy", action: #selector(quit), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)
        menu.autoenablesItems = false
        menu.delegate = self
        statusItem.menu = menu
        updateMenu()
    }

    func menuWillOpen(_ menu: NSMenu) { updateMenu() }

    private func updateMenu() {
        let name = app.assistantName
        if !app.isPaired {
            statusLine?.title = "Not connected — open Settings to pair"
        } else if let fix = app.status?.checks.first(where: { !$0.ok && $0.id != "brief" })?.fix {
            statusLine?.title = fix
        } else if app.status == nil {
            statusLine?.title = app.lastError ?? "Connecting to \(name)…"
        } else {
            statusLine?.title = "\(name) is ready"
        }
        callItem?.title = (active ? "End call" : "Start call") + " (\(app.callShortcut.display))"
        callItem?.isEnabled = app.isPaired
        if let pauseItem {
            let shortcut = pauseShortcut.map { " (\($0.display))" } ?? ""
            pauseItem.title = (native.isPaused ? "Resume call" : "Pause call") + shortcut
            pauseItem.isEnabled = active && (native.isPaused || native.micControllable)
            pauseItem.toolTip = pauseShortcutProblem
        }
        if let muteItem {
            let shortcut = muteShortcut.map { " (\($0.display); hold to talk)" } ?? ""
            muteItem.title = (native.micMuted ? "Unmute microphone" : "Mute microphone") + shortcut
            muteItem.isEnabled = active && native.micControllable
            muteItem.toolTip = muteShortcutProblem
        }
    }

    @objc private func resetPanelPosition() { native.panel.resetPlacement() }

    @objc private func quit() {
        if active { quitPending = true; endConversation() } else { NSApp.terminate(nil) }
    }

    @objc private func showRecentWork() {
        setBadge(false)
        native.showWork(active: active)
    }

    // MARK: Call

    /// Menu Start/End: explicit, never hides-only.
    @objc func toggleConversation() {
        if active { endConversation() } else { startConversation() }
    }

    /// The global hotkey: start when idle and hidden, hide when idle and shown, end during a call.
    private func hotkeyPressed() {
        let connection = native.model.state.connection
        switch hotkeyAction(connection: active ? connection : (connection.isOpen ? connection : .idle),
                            panelVisible: native.panelVisible) {
        case .startCall: startConversation()
        case .endCall: endConversation()
        case .hidePanel: native.closePanel()
        case .showPanel: native.showPanel()
        }
    }

    private func startConversation() {
        guard app.isPaired else { showOnboarding(); return }
        active = true
        idle?.callStarted()
        native.start()
        registerMuteHotKey()
        registerPauseHotKey()
        updateMenu()
    }

    private func endConversation() {
        guard active else { return }
        native.end()
    }

    private func configureClient() {
        native.onPauseChanged = { [weak self] paused in
            guard let self else { return }
            if paused { self.unregisterMuteHotKey() } else { self.registerMuteHotKey() }
            self.updateMenu()
        }
        native.model.onStart = { [weak self] in self?.startConversation() }
        native.onPausedTaskSettled = { [weak self] notice in
            guard let self else { return }
            if let idle = self.idle { idle.pausedNotice(notice) } else { self.setBadge(true) }
        }
        native.onClosed = { [weak self] in
            guard let self else { return }
            self.active = false
            self.unregisterMuteHotKey()
            self.pauseHotKey = nil
            self.updateMenu()
            let state = self.native.model.state
            // A delegated run whose first work update never arrived is still in flight.
            self.idle?.callEnded(lastWork: state.workInfo ?? state.runID.map { WorkInfo(runID: $0, status: "running") })
            if self.quitPending { NSApp.terminate(nil) }
        }
    }

    // MARK: Hotkeys

    private func registerCallHotKey(_ shortcut: KeyShortcut) {
        hotKey = nil
        do {
            hotKey = try GlobalHotKey(keyCode: shortcut.keyCode, modifiers: shortcut.modifiers, display: shortcut.display) { [weak self] in
                self?.hotkeyPressed()
            }
            app.callShortcutProblem = nil
        } catch {
            app.callShortcutProblem = error.localizedDescription
        }
        native.setCallShortcutHint(shortcut.spoken)
        updateMenu()
    }

    /// `defaults write <bundle id> muteShortcut "ctrl+opt+m"` (empty disables).
    private func resolveMuteShortcut() {
        let raw = UserDefaults.standard.string(forKey: Prefs.muteShortcut)
        if raw?.trimmingCharacters(in: .whitespaces).isEmpty == true {
            muteShortcut = nil; muteShortcutProblem = "Mute shortcut disabled"
        } else {
            switch raw.map({ KeyShortcut.parse($0, reserved: app.callShortcut) }) ?? .success(.defaultMute) {
            case .failure(let error):
                muteShortcut = nil; muteShortcutProblem = "Mute shortcut '\(raw ?? "")' rejected: \(error)"
            case .success(let shortcut):
                if GlobalHotKey.collidesWithSystemShortcut(keyCode: shortcut.keyCode, modifiers: shortcut.modifiers) {
                    muteShortcut = nil; muteShortcutProblem = "Mute shortcut \(shortcut.display) is used by a macOS system shortcut"
                } else {
                    muteShortcut = shortcut; muteShortcutProblem = nil
                }
            }
        }
        native.model.muteShortcutHint = muteShortcut.map { "\($0.display): tap to mute/unmute, hold to talk" } ?? ""
    }

    /// `defaults write <bundle id> pauseShortcut "ctrl+opt+p"` (empty disables).
    private func resolvePauseShortcut() {
        let raw = UserDefaults.standard.string(forKey: Prefs.pauseShortcut)
        if raw?.trimmingCharacters(in: .whitespaces).isEmpty == true {
            pauseShortcut = nil; pauseShortcutProblem = "Pause shortcut disabled"
        } else {
            switch raw.map({ KeyShortcut.parse($0, reserved: app.callShortcut) }) ?? .success(.defaultPause) {
            case .failure(let error):
                pauseShortcut = nil; pauseShortcutProblem = "Pause shortcut '\(raw ?? "")' rejected: \(error)"
            case .success(let shortcut):
                if shortcut == muteShortcut {
                    pauseShortcut = nil; pauseShortcutProblem = "Pause shortcut \(shortcut.display) is the mute shortcut"
                } else if GlobalHotKey.collidesWithSystemShortcut(keyCode: shortcut.keyCode, modifiers: shortcut.modifiers) {
                    pauseShortcut = nil; pauseShortcutProblem = "Pause shortcut \(shortcut.display) is used by a macOS system shortcut"
                } else {
                    pauseShortcut = shortcut; pauseShortcutProblem = nil
                }
            }
        }
        native.model.pauseShortcutHint = pauseShortcut.map { "\($0.display): pause or resume the call" } ?? ""
    }

    private func registerPauseHotKey() {
        guard pauseHotKey == nil, native.supportsPause, let shortcut = pauseShortcut else { return }
        do {
            pauseHotKey = try GlobalHotKey(keyCode: shortcut.keyCode, modifiers: shortcut.modifiers,
                                           display: shortcut.display, exclusive: true,
                                           callback: { [weak self] in self?.togglePause() })
        } catch {
            pauseShortcutProblem = error.localizedDescription
        }
    }

    private func togglePause() {
        guard active else { return }
        native.togglePause()
        updateMenu()
    }

    @objc private func togglePauseFromMenu() { togglePause() }

    private func registerMuteHotKey() {
        guard muteHotKey == nil, let shortcut = muteShortcut else { return }
        muteGesture.cancel()
        do {
            muteHotKey = try GlobalHotKey(keyCode: shortcut.keyCode, modifiers: shortcut.modifiers,
                                          display: shortcut.display, exclusive: true,
                                          onRelease: { [weak self] in self?.muteKeyUp() },
                                          callback: { [weak self] in self?.muteKeyDown() })
        } catch {
            muteShortcutProblem = error.localizedDescription
        }
    }

    private func unregisterMuteHotKey() {
        muteHotKey = nil
        muteGesture.cancel()
    }

    private func muteKeyDown() {
        guard active, native.micControllable,
              let next = muteGesture.press(current: native.micMuted ? .muted : .live, now: Date()) else { return }
        native.setMicMuted(next == .muted)
        updateMenu()
    }

    private func muteKeyUp() {
        guard let restore = muteGesture.release(now: Date()) else { return }
        guard active, native.micControllable else { return }
        native.setMicMuted(restore == .muted)
        updateMenu()
    }

    @objc private func toggleMuteFromMenu() {
        guard active, native.micControllable else { return }
        native.setMicMuted(!native.micMuted)
        updateMenu()
    }

    // MARK: Previews (offline; no network)

    private func runPreview(_ args: [String], index: Int) {
        let name = index + 1 < args.count ? args[index + 1] : "listening"
        guard let fixture = PreviewFixtures.state(name) else {
            print("unknown --ui-preview state '\(name)'; expected one of: \(PreviewFixtures.names.joined(separator: ", "))")
            exit(2)
        }
        let preview = NativeVoiceClient(config: app.config)
        native = preview
        if name == "image" { preview.model.loadProductImage = { _, _ in PreviewFixtures.sampleImage() } }
        preview.showPreview(fixture.0, workExpanded: fixture.workExpanded)
        print("ui-preview \(name) window=\(preview.panel.windowNumber)")
        if let appearance = args.firstIndex(of: "--appearance").flatMap({ $0 + 1 < args.count ? args[$0 + 1] : nil }) {
            NSApp.appearance = NSAppearance(named: appearance == "light" ? .aqua : .darkAqua)
        }
        if let snap = args.firstIndex(of: "--snapshot"), snap + 1 < args.count {
            let url = URL(fileURLWithPath: args[snap + 1])
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
                do { try preview.panel.snapshot(to: url); print("snapshot \(url.path)"); exit(0) }
                catch { print("snapshot failed: \(error)"); exit(1) }
            }
        }
    }
}

extension PairingLink.ParseError {
    var userMessage: String {
        switch self {
        case .notSpeakeasy, .notPairing: return "This isn't a Speakeasy pairing link."
        case .missingServer: return "The link doesn't say which server to connect to."
        case .untrustedServer: return "The link points to a server Speakeasy won't trust. Use this Mac (127.0.0.1) or an https Tailscale address."
        case .invalidCode: return "The link's pairing code isn't a 6-digit code. Run hermes voice pair for a new one."
        }
    }
}

extension KeyShortcut {
    /// "Control–Option–Space" for the idle panel hint.
    var spoken: String {
        var parts: [String] = []
        if modifiers & Self.control != 0 { parts.append("Control") }
        if modifiers & Self.option != 0 { parts.append("Option") }
        if modifiers & Self.shift != 0 { parts.append("Shift") }
        if modifiers & Self.command != 0 { parts.append("Command") }
        return (parts + [keyName]).joined(separator: "–")
    }
}
