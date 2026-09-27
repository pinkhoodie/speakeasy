import AppKit
import AVFoundation
import SpeakeasyCore
import SwiftUI

/// The guided first run, one screen per step:
/// connect → microphone → voice sign-in → names → where finished work goes → voice brief → hotkey + try it.
@MainActor
final class OnboardingFlow: ObservableObject {
    enum Step: Int, CaseIterable {
        case connect, microphone, voice, names, delivery, brief, hotkey
        var title: String {
            switch self {
            case .connect: return "Connect to Hermes"
            case .microphone: return "Microphone"
            case .voice: return "Voice sign-in"
            case .names: return "Names"
            case .delivery: return "Finished work"
            case .brief: return "Voice brief"
            case .hotkey: return "Try it"
            }
        }
    }

    @Published var step: Step = .connect
    @Published var server = AppConfig.localServerURL.absoluteString
    @Published var code = ""
    @Published var pairing = false
    @Published var pairError: String?
    @Published var assistantName = ""
    @Published var userName = ""
    @Published var target: String?
    @Published var continuity = true
    @Published var saving = false
    @Published var saveError: String?

    let app: AppModel
    var onFinish: () -> Void = {}

    init(app: AppModel) {
        self.app = app
        if let saved = app.config.serverURL { server = saved.absoluteString }
        if app.isPaired { step = .microphone }
    }

    func pairFromLink(_ link: PairingLink) {
        server = link.server.absoluteString
        code = link.code
        step = .connect
        pair()
    }

    func pair() {
        guard let url = URL(string: server.trimmingCharacters(in: .whitespacesAndNewlines)) else {
            pairError = "Enter a server address like http://127.0.0.1:8795"; return
        }
        pairing = true; pairError = nil
        Task {
            do {
                try await app.pair(server: url, code: code)
                pairing = false
                await app.refresh()
                loadNames()
                next()
            } catch {
                pairing = false
                pairError = app.describe(error)
            }
        }
    }

    func loadNames() {
        let s = app.settings
        if assistantName.isEmpty { assistantName = s.assistantName ?? app.status?.assistantName ?? "" }
        if userName.isEmpty { userName = s.userName ?? "" }
        if target == nil { target = s.deliveryTarget ?? app.suggestedDestination }
        continuity = s.continuity?.enabled ?? true
    }

    /// Steps `hermes voice setup` or the system already finished are skipped, so a normal setup
    /// shows: names, where results go, shortcut (plus microphone the first time).
    func isAlreadyDone(_ s: Step) -> Bool {
        switch s {
        case .connect: return app.isPaired
        case .microphone: return app.micAuthorization == .authorized
        case .voice: return app.status?.voiceReady == true
        case .brief: return true  // written in the background; shown and editable in Settings
        default: return false
        }
    }

    func next() {
        var n = Step(rawValue: step.rawValue + 1)
        while let candidate = n, isAlreadyDone(candidate) { n = Step(rawValue: candidate.rawValue + 1) }
        if let n { step = n } else { finish() }
        if step == .names { loadNames() }
    }

    /// First step that still needs the user.
    func start() {
        step = .connect
        if isAlreadyDone(.connect) { next() }
    }

    func back() {
        var p = Step(rawValue: step.rawValue - 1)
        while let candidate = p, isAlreadyDone(candidate) { p = Step(rawValue: candidate.rawValue - 1) }
        if let p { step = p }
    }

    /// Names, delivery and continuity are saved together (`POST /voice/onboarding`): after the
    /// delivery step, and again from the last step when continuity is changed there.
    func saveProfile(then advance: Bool = true) {
        saving = true; saveError = nil
        Task {
            let ok = await app.completeOnboarding(assistantName: assistantName.trimmingCharacters(in: .whitespaces),
                                                  userName: userName.trimmingCharacters(in: .whitespaces),
                                                  target: target, continuity: continuity)
            saving = false
            if ok { if advance { next() } } else { saveError = app.lastError ?? "Couldn't save" }
        }
    }

    func finish() { onFinish() }
}

@MainActor
final class OnboardingWindowController: NSObject, NSWindowDelegate {
    let flow: OnboardingFlow
    private var window: NSWindow?
    var onFinish: () -> Void = {}

    init(app: AppModel) {
        flow = OnboardingFlow(app: app)
        super.init()
        flow.onFinish = { [weak self] in
            self?.window?.close()
        }
    }

    func show() {
        if window == nil {
            Task { await flow.app.refresh(); flow.start() }
            let hosting = NSHostingController(rootView: OnboardingView(flow: flow).environmentObject(flow.app))
            let w = NSWindow(contentViewController: hosting)
            w.title = "Welcome to Speakeasy"
            w.styleMask = [.titled, .closable]
            w.setContentSize(NSSize(width: 520, height: 460))
            w.isReleasedWhenClosed = false
            w.delegate = self
            w.center()
            window = w
        }
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }

    func windowWillClose(_ notification: Notification) {
        // Closing early is fine: every step is also in Settings. Only a paired Mac counts as done.
        if flow.app.isPaired { onFinish() }
        window = nil
    }
}

struct OnboardingView: View {
    @ObservedObject var flow: OnboardingFlow
    @EnvironmentObject var app: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 6) {
                ForEach(OnboardingFlow.Step.allCases, id: \.self) { step in
                    Capsule().fill(step.rawValue <= flow.step.rawValue ? Color.accentColor : Color.secondary.opacity(0.25))
                        .frame(height: 4)
                }
            }
            .accessibilityElement()
            .accessibilityLabel("Step \(flow.step.rawValue + 1) of \(OnboardingFlow.Step.allCases.count): \(flow.step.title)")
            .padding(.bottom, 18)

            Group {
                switch flow.step {
                case .connect: ConnectStep(flow: flow)
                case .microphone: MicrophoneStep(flow: flow)
                case .voice: VoiceSignInStep(flow: flow)
                case .names: NamesStep(flow: flow)
                case .delivery: DeliveryStep(flow: flow)
                case .brief: BriefStep(flow: flow)
                case .hotkey: HotkeyStep(flow: flow)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
        .padding(24)
        .frame(width: 520, height: 460)
    }
}

private struct StepHeader: View {
    var title: String
    var subtitle: String
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.title2.weight(.semibold))
            Text(subtitle).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
        .padding(.bottom, 14)
    }
}

private struct StepButtons: View {
    var back: (() -> Void)?
    var skip: (() -> Void)?
    var primary: String
    var primaryDisabled = false
    var busy = false
    var action: () -> Void
    var body: some View {
        HStack {
            if let back { Button("Back", action: back) }
            Spacer()
            if busy { ProgressView().controlSize(.small) }
            if let skip { Button("Skip", action: skip) }
            Button(primary, action: action)
                .keyboardShortcut(.defaultAction)
                .disabled(primaryDisabled || busy)
        }
        .padding(.top, 12)
    }
}

// MARK: (a) Connect

private struct ConnectStep: View {
    @ObservedObject var flow: OnboardingFlow
    @EnvironmentObject var app: AppModel
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            StepHeader(title: "Connect to Hermes",
                       subtitle: "Run hermes voice setup on the Mac that runs Hermes. It opens a pairing link that connects this app automatically. On another Mac, run hermes voice pair and enter the address and code here.")
            Form {
                TextField("Server address", text: $flow.server, prompt: Text("http://127.0.0.1:8795"))
                    .accessibilityHint("This Mac, or an https Tailscale address ending in .ts.net")
                TextField("Pairing code", text: $flow.code, prompt: Text("6 digits"))
                    .font(.system(.body, design: .monospaced))
                    .onSubmit { if normalizedPairingCode(flow.code) != nil { flow.pair() } }
            }
            .formStyle(.grouped)
            .frame(height: 130)
            if let error = flow.pairError {
                Label(error, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let host = URL(string: flow.server)?.host {
                Label(host.hasSuffix(".ts.net") ? "Connecting over Tailscale: \(host)" : "Local only: this Mac",
                      systemImage: host.hasSuffix(".ts.net") ? "lock.shield.fill" : "desktopcomputer")
                    .font(.headline)
            }
            if let offer = app.tailnetOffer { TailnetOffer(url: offer) }
            Spacer()
            StepButtons(primary: "Connect", primaryDisabled: normalizedPairingCode(flow.code) == nil, busy: flow.pairing,
                        action: flow.pair)
        }
    }
}

// MARK: (b) Microphone

private struct MicrophoneStep: View {
    @ObservedObject var flow: OnboardingFlow
    @EnvironmentObject var app: AppModel
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            StepHeader(title: "Microphone",
                       subtitle: "Speakeasy listens only during a call you start. Audio goes to your voice provider through your own Hermes server.")
            if let status = app.status {
                Label(status.reachability, systemImage: status.tailscaleName?.isEmpty == false ? "lock.shield.fill" : "desktopcomputer")
                    .font(.headline)
            }
            switch app.micAuthorization {
            case .authorized:
                Label("Microphone access is on", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
            case .denied, .restricted:
                Label("Microphone access is off", systemImage: "mic.slash.fill").foregroundStyle(.orange)
                Button("Open Privacy Settings") { AppModel.openMicrophonePrivacySettings() }
            default:
                Button("Allow microphone") { Task { _ = await app.requestMicrophone() } }
            }
            Spacer()
            StepButtons(back: nil, primary: "Continue", action: flow.next)
        }
    }
}

// MARK: (c) Voice sign-in

private struct VoiceSignInStep: View {
    @ObservedObject var flow: OnboardingFlow
    @EnvironmentObject var app: AppModel
    @State private var showAPIKeyHelp = false
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            StepHeader(title: "Voice sign-in",
                       subtitle: "By default Speakeasy uses your ChatGPT sign-in through Codex on the Hermes machine. No API key needed.")
            if let status = app.status {
                ForEach(status.checks.filter { $0.id != "brief" }) { check in
                    HStack(alignment: .top) {
                        Image(systemName: check.ok ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
                            .foregroundStyle(check.ok ? .green : .orange)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(check.title).fontWeight(.medium)
                            if let fix = check.fix {
                                Text(fix).font(.callout).foregroundStyle(.secondary).textSelection(.enabled)
                            }
                        }
                    }
                    .accessibilityElement(children: .combine)
                }
                if status.codexSignedIn != true && status.resolvedProvider == .codex {
                    Text("On the Hermes machine, run hermes voice setup in Terminal. It signs you in to ChatGPT (a browser window opens). Then press Check again.")
                        .font(.callout).fixedSize(horizontal: false, vertical: true)
                }
            } else {
                Text(app.lastError ?? "Checking…").foregroundStyle(.secondary)
            }
            DisclosureGroup("Use an API key instead", isExpanded: $showAPIKeyHelp) {
                Text("On the Hermes machine, run hermes voice setup --api-key and paste your OpenAI API key when asked. It's stored only in that Hermes profile, never on this Mac, and calls bill that OpenAI account.")
                    .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
            Spacer()
            HStack {
                Button("Check again") { Task { await app.refresh() } }
                Spacer()
            }
            StepButtons(back: flow.back, primary: "Continue", busy: app.refreshing, action: flow.next)
        }
        .task { await app.refresh() }
    }
}

// MARK: (d) Names

private struct NamesStep: View {
    @ObservedObject var flow: OnboardingFlow
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            StepHeader(title: "Names", subtitle: "How you and your assistant talk to each other.")
            Form {
                TextField("What do you call your assistant?", text: $flow.assistantName, prompt: Text(VoiceState.defaultAssistantName))
                TextField("What should it call you?", text: $flow.userName, prompt: Text("Optional"))
            }
            .formStyle(.grouped)
            .frame(height: 120)
            Spacer()
            StepButtons(back: flow.back, primary: "Continue", action: flow.next)
        }
    }
}

// MARK: (e) Delivery

private struct DeliveryStep: View {
    @ObservedObject var flow: OnboardingFlow
    @EnvironmentObject var app: AppModel
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            StepHeader(title: "Where should finished work go?",
                       subtitle: "When a task finishes after you hang up, \(flow.assistantName.isEmpty ? app.assistantName : flow.assistantName) can send the result to one of your connected Hermes chats.")
            Form {
                DeliveryPicker(target: $flow.target, destinations: app.destinations)
            }
            .formStyle(.grouped)
            .frame(height: 80)
            Text("You can add more channels later in Settings › Delivery, each with a topic, so a task goes where it belongs. Suggest channels asks your Hermes to propose some.")
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            SuggestChannelsButton()
            if let error = flow.saveError { Text(error).foregroundStyle(.orange) }
            Spacer()
            StepButtons(back: flow.back, primary: "Continue", busy: flow.saving, action: { flow.saveProfile() })
        }
        .task { if app.destinations.isEmpty { await app.refresh() } }
    }
}

struct DeliveryPicker: View {
    @Binding var target: String?
    var destinations: [Destination]
    var body: some View {
        Picker("Send finished work to", selection: $target) {
            Text("Nowhere (notifications on this Mac only)").tag(String?.none)
            ForEach(destinations) { d in Text(d.label).tag(String?.some(d.target)) }
            if let target, !destinations.contains(where: { $0.target == target }) {
                Text(target).tag(String?.some(target))
            }
        }
    }
}

// MARK: (f) Brief

private struct BriefStep: View {
    @ObservedObject var flow: OnboardingFlow
    @EnvironmentObject var app: AppModel
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            StepHeader(title: "Your assistant is writing a short brief about you",
                       subtitle: "Your Hermes writes what the voice model should know — how to address you, what you're working on — from what it already knows. You can read and edit it in Settings › Voice brief.")
            HStack(spacing: 8) {
                if app.brief?.state == "writing" || app.brief?.state == "pending" { ProgressView().controlSize(.small) }
                Text(briefLine).foregroundStyle(.secondary)
            }
            if let text = app.brief?.text, !text.isEmpty {
                ScrollView {
                    Text(text).font(.callout).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxHeight: 180)
                .padding(8)
                .background(RoundedRectangle(cornerRadius: 8).fill(Color.primary.opacity(0.05)))
            }
            Spacer()
            StepButtons(back: flow.back, skip: flow.next, primary: "Continue", action: flow.next)
        }
        .task {
            // Poll gently while the brief is being written.
            for _ in 0..<30 {
                await app.refreshBrief()
                if let state = app.brief?.state, state != "writing" && state != "pending" { break }
                try? await Task.sleep(nanoseconds: 4_000_000_000)
            }
        }
    }

    private var briefLine: String {
        switch app.brief?.state {
        case "writing", "pending": return "Writing… calls work meanwhile."
        case "ready", "edited": return "Ready."
        case "failed": return "Couldn't write it this time. Calls still work; try Rewrite in Settings later."
        default: return "Not started yet. Calls still work."
        }
    }
}

// MARK: (g) Hotkey + try it

private struct HotkeyStep: View {
    @ObservedObject var flow: OnboardingFlow
    @EnvironmentObject var app: AppModel
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            StepHeader(title: "Talk any time",
                       subtitle: "Press the shortcut to start a call; press it again to end. Change it here or later in Settings › General.")
            HStack {
                Text("Call shortcut")
                Spacer()
                ShortcutRecorder(shortcut: app.callShortcut) { app.setCallShortcut($0) }
            }
            if let problem = app.callShortcutProblem { Text(problem).foregroundStyle(.orange) }
            VStack(alignment: .leading, spacing: 4) {
                Toggle("Continue existing conversations", isOn: Binding(get: { flow.continuity },
                                                                         set: { flow.continuity = $0; flow.saveProfile(then: false) }))
                Text(continuityHelp).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            .padding(.vertical, 4)
            if let error = flow.saveError { Text(error).foregroundStyle(.orange) }
            Text("Your first call starts with a one-minute tour. Say \u{201C}skip\u{201D} or press Skip tour to jump straight in.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Button {
                flow.finish()  // marks the tour pending before the call reads it
                app.startCall()
            } label: {
                Label("Try it — start a call", systemImage: "waveform")
            }
            .controlSize(.large)
            .disabled(!app.isPaired)
            Spacer()
            StepButtons(back: flow.back, primary: "Done", action: flow.finish)
        }
    }
}

/// Onboarding's Suggest channels: asks Hermes, lets the user pick, and saves picked channels.
private struct SuggestChannelsButton: View {
    @EnvironmentObject var app: AppModel
    @State private var busy = false
    @State private var items: [ServerSettings.Channel]?
    @State private var error: String?
    @State private var added = 0

    var body: some View {
        HStack(spacing: 8) {
            Button { run() } label: { busy ? AnyView(ProgressView().controlSize(.small)) : AnyView(Text("Suggest channels")) }
                .disabled(busy || app.destinations.isEmpty)
            if let error { Text(error).font(.caption).foregroundStyle(.orange).lineLimit(2) }
            else if added > 0 { Text("Added \(added) channel\(added == 1 ? "" : "s").").font(.caption).foregroundStyle(.secondary) }
        }
        .sheet(item: Binding(get: { items.map { SuggestionPick(items: $0) } }, set: { items = $0?.items })) { pick in
            SuggestionsSheet(items: pick.items, existing: app.settings.delivery?.channels ?? [],
                             canThread: { app.status?.canOpenThread(in: $0) == true }) { picked in
                Task { await save(picked) }
            }
        }
    }

    private func run() {
        busy = true; error = nil
        Task {
            let result = await app.suggestChannels()
            busy = false
            if let result, !result.isEmpty { items = result } else { error = app.lastError ?? "Hermes didn't suggest any channels." }
        }
    }

    private func save(_ picked: [ServerSettings.Channel]) async {
        var s = app.settings
        var d = s.delivery ?? .init()
        let before = d.channels?.count ?? 0
        d.channels = ChannelSuggestions.merge(picked, into: d.channels ?? [])
        s.delivery = d
        if await app.save(s) { added = (d.channels?.count ?? 0) - before } else { error = app.lastError }
    }
}

private struct SuggestionPick: Identifiable {
    let items: [ServerSettings.Channel]
    var id: String { items.map(\.target).joined(separator: ",") }
}
