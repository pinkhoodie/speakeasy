import AppKit
import SpeakeasyCore
import SwiftUI

/// Settings window. Server-side values load from and save to `/voice/settings`;
/// client values live in UserDefaults (`@AppStorage`).
struct SettingsView: View {
    @EnvironmentObject var app: AppModel

    var body: some View {
        TabView {
            GeneralSettings().tabItem { Label("General", systemImage: "gearshape") }
            VoiceSettings().tabItem { Label("Voice", systemImage: "waveform") }
            BriefSettings().tabItem { Label("Voice brief", systemImage: "text.quote") }
            BehaviorSettings().tabItem { Label("Behavior", systemImage: "slider.horizontal.3") }
            DeliverySettings().tabItem { Label("Delivery", systemImage: "paperplane") }
            ConnectionSettings().tabItem { Label("Connection", systemImage: "network") }
            AboutSettings().tabItem { Label("About", systemImage: "info.circle") }
        }
        .frame(width: 560, height: 470)
        .task { await app.refresh() }
    }
}

/// A draft copy of the server settings with Save / Revert.
private struct ServerForm<Content: View>: View {
    @EnvironmentObject var app: AppModel
    @Binding var draft: ServerSettings
    @State private var saving = false
    @State private var saved = false
    @ViewBuilder var content: () -> Content

    var body: some View {
        VStack(spacing: 0) {
            Form { content() }.formStyle(.grouped)
            HStack {
                if let error = app.lastError { Text(error).font(.caption).foregroundStyle(.orange).lineLimit(2) }
                else if saved { Text("Saved").font(.caption).foregroundStyle(.secondary) }
                Spacer()
                Button("Revert") { draft = app.settings }.disabled(draft == app.settings || saving)
                Button("Save") {
                    saving = true
                    Task {
                        saved = await app.save(draft)
                        saving = false
                        if saved { draft = app.settings }
                    }
                }
                .keyboardShortcut("s", modifiers: .command)
                .disabled(draft == app.settings || saving || !app.isPaired)
            }
            .padding(.horizontal, 20).padding(.bottom, 14)
        }
        .onAppear { draft = app.settings }
        .onReceive(app.$settings) { new in if !saving { draft = new } }
        .disabled(!app.isPaired)
    }
}

// MARK: General

private struct GeneralSettings: View {
    @EnvironmentObject var app: AppModel
    @AppStorage(Prefs.startMuted) private var startMuted = false
    @AppStorage(Prefs.showPanelOnStart) private var showPanelOnStart = true
    @AppStorage(Prefs.followSystemAudio) private var followSystemAudio = true
    @AppStorage(Prefs.startSlim) private var startSlim = false

    var body: some View {
        Form {
            LabeledContent("Call shortcut") {
                ShortcutRecorder(shortcut: app.callShortcut) { app.setCallShortcut($0) }
            }
            if let problem = app.callShortcutProblem { Text(problem).foregroundStyle(.orange) }
            Toggle("Start calls muted", isOn: $startMuted)
            Toggle("Show the panel when a call starts", isOn: $showPanelOnStart)
            Toggle("Start calls in slim mode", isOn: $startSlim)
                .help("Just the controls and a one-line task summary; expand any time")
            Toggle("Follow the system's audio devices (AirPods etc.)", isOn: $followSystemAudio)
            Toggle("Launch at login", isOn: Binding(get: { app.launchAtLogin }, set: { app.launchAtLogin = $0 }))
            Section {
                LabeledContent("Microphone") {
                    switch app.micAuthorization {
                    case .authorized: Text("Allowed").foregroundStyle(.secondary)
                    case .notDetermined: Button("Allow…") { Task { _ = await app.requestMicrophone() } }
                    default: Button("Open Privacy Settings") { AppModel.openMicrophonePrivacySettings() }
                    }
                }
            }
        }
        .formStyle(.grouped)
    }
}

// MARK: Voice

private struct VoiceSettings: View {
    @EnvironmentObject var app: AppModel
    @State private var draft = ServerSettings()

    var body: some View {
        ServerForm(draft: $draft) {
            TextField("Assistant name", text: Binding(get: { draft.assistantName ?? "" },
                                                      set: { draft.assistantName = $0.isEmpty ? nil : $0 }),
                      prompt: Text(VoiceState.defaultAssistantName))
            TextField("What it calls you", text: Binding(get: { draft.userName ?? "" },
                                                         set: { draft.userName = $0.isEmpty ? nil : $0 }),
                      prompt: Text("Optional"))
            Picker("Voice provider", selection: Binding(get: { draft.resolvedProvider },
                                                         set: { p in var v = draft.voice ?? .init(); v.provider = p.rawValue; draft.voice = v })) {
                ForEach(VoiceProvider.allCases) { Text($0.label).tag($0) }
            }
            providerStatus
            Picker("Voice", selection: Binding(get: { draft.voice?.voice ?? "" },
                                               set: { v in var voice = draft.voice ?? .init(); voice.voice = v.isEmpty ? nil : v; draft.voice = voice })) {
                Text("Default").tag("")
                ForEach(ServerSettings.knownVoices, id: \.self) { Text($0.capitalized).tag($0) }
                if let current = draft.voice?.voice, !current.isEmpty, !ServerSettings.knownVoices.contains(current) {
                    Text(current).tag(current)
                }
            }
        }
    }

    @ViewBuilder private var providerStatus: some View {
        switch draft.resolvedProvider {
        case .codex:
            LabeledContent("ChatGPT sign-in") {
                if let signed = app.status?.codexSignedIn {
                    Text(signed ? "Signed in" : "Not signed in — run codex login on the Hermes machine")
                        .foregroundStyle(signed ? Color.secondary : Color.orange)
                } else { Text("Unknown").foregroundStyle(.secondary) }
            }
        case .openai:
            LabeledContent("OpenAI API key") {
                if let set = app.status?.apiKeySet {
                    Text(set ? "Set on the server" : "Not set — add SPEAKEASY_OPENAI_API_KEY to the Hermes .env")
                        .foregroundStyle(set ? Color.secondary : Color.orange)
                } else { Text("Unknown").foregroundStyle(.secondary) }
            }
            Text("The key stays on the Hermes machine. Speakeasy never asks for it or stores it on this Mac.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}

// MARK: Voice brief

private struct BriefSettings: View {
    @EnvironmentObject var app: AppModel
    @State private var text = ""
    @State private var dirty = false
    @State private var draft = ServerSettings()
    @State private var busy = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(stateLine).font(.callout).foregroundStyle(.secondary)
                Spacer()
                Button("Rewrite") {
                    busy = true
                    Task { await app.rewriteBrief(); busy = false; dirty = false }
                }
                .help("Ask your Hermes to write the brief again from what it knows now")
                .disabled(busy || !app.isPaired)
                Button("Save") {
                    busy = true
                    Task { if await app.saveBrief(text) { dirty = false }; busy = false }
                }
                .disabled(!dirty || busy)
            }
            TextEditor(text: $text)
                .font(.system(.body, design: .default))
                .frame(minHeight: 180)
                .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.secondary.opacity(0.3)))
                .onChange(of: text) { _, new in dirty = new != (app.brief?.text ?? "") }
                .accessibilityLabel("Voice brief text")
            ServerForm(draft: $draft) {
                Toggle("Keep the brief fresh automatically", isOn: Binding(
                    get: { draft.brief?.autoRefresh ?? true },
                    set: { v in var b = draft.brief ?? .init(); b.autoRefresh = v; draft.brief = b }))
                Toggle("Include recent voice conversations", isOn: Binding(
                    get: { draft.brief?.includeRecentVoice ?? true },
                    set: { v in var b = draft.brief ?? .init(); b.includeRecentVoice = v; draft.brief = b }))
                TextField("Extra instructions", text: Binding(get: { draft.instructionsExtra ?? "" },
                                                              set: { draft.instructionsExtra = $0.isEmpty ? nil : $0 }),
                          prompt: Text("e.g. Keep answers under two sentences"), axis: .vertical)
                    .lineLimit(2...4)
            }
            .frame(height: 190)
        }
        .padding([.top, .horizontal], 16)
        .onAppear { text = app.brief?.text ?? "" }
        .onReceive(app.$brief) { b in if !dirty { text = b?.text ?? "" } }
    }

    private var stateLine: String {
        guard let brief = app.brief else { return app.isPaired ? "Loading…" : "Not connected" }
        var parts: [String] = []
        switch brief.state {
        case "writing", "pending": parts.append("Writing…")
        case "ready": parts.append("Ready")
        case "failed": parts.append("Last rewrite failed")
        case let s?: parts.append(s.capitalized)
        case nil: break
        }
        if brief.edited { parts.append("edited by you") }
        if let at = brief.updatedAt { parts.append("updated \(at.formatted(.relative(presentation: .named)))") }
        return parts.joined(separator: " · ")
    }
}

// MARK: Behavior

private struct BehaviorSettings: View {
    @State private var draft = ServerSettings()

    var body: some View {
        ServerForm(draft: $draft) {
            Section {
                Text("Several requests can run at once. Each becomes its own task in the panel; follow-ups go to the right one.")
                    .font(.callout).foregroundStyle(.secondary)
            }
            Toggle("Continue existing conversations", isOn: Binding(
                get: { draft.continuity?.enabled ?? true },
                set: { v in var c = draft.continuity ?? .init(); c.enabled = v; draft.continuity = c }))
                .help("Pick up where you left off: the next call hears what happened while you were away")
            Stepper(value: Binding(get: { Int(draft.idlePauseMinutes ?? 5) }, set: { draft.idlePauseMinutes = Double($0) }),
                    in: 0...60) {
                let minutes = Int(draft.idlePauseMinutes ?? 5)
                Text(minutes == 0 ? "Auto-pause a quiet call: off" : "Auto-pause a quiet call after \(minutes) min")
            }
        }
    }
}

// MARK: Delivery

private struct DeliverySettings: View {
    @EnvironmentObject var app: AppModel
    @State private var draft = ServerSettings()

    var body: some View {
        ServerForm(draft: $draft) {
            Text("When a task finishes after the call ended, send the result here.")
                .font(.callout).foregroundStyle(.secondary)
            DeliveryPicker(target: Binding(get: { draft.deliveryTarget }, set: { t in
                var d = draft.delivery ?? .init(); d.target = t; if t == nil { d.newThreadPerTask = false }
                draft.delivery = d
                draft.notifyTarget = t
            }), destinations: app.destinations)
            if draft.deliveryTarget != nil && app.status?.threadsSupported == true {
                Toggle("Open a new thread for each task", isOn: Binding(
                    get: { draft.delivery?.newThreadPerTask ?? false },
                    set: { v in var d = draft.delivery ?? .init(); d.newThreadPerTask = v; draft.delivery = d }))
            }
            if app.destinations.isEmpty {
                Text("No connected Hermes chats found. Connect one in Hermes (e.g. Telegram), then reopen Settings.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }
}

// MARK: Connection

private struct ConnectionSettings: View {
    @EnvironmentObject var app: AppModel
    @State private var confirmUnpair = false

    var body: some View {
        Form {
            LabeledContent("Server", value: app.config.serverURL?.absoluteString ?? "—")
            LabeledContent("This Mac", value: app.isPaired ? (app.deviceName ?? "Paired") : "Not paired")
            Section("Status") {
                if let status = app.status {
                    ForEach(status.checks) { check in
                        HStack(alignment: .top) {
                            Image(systemName: check.ok ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
                                .foregroundStyle(check.ok ? .green : .orange)
                            VStack(alignment: .leading) {
                                Text(check.title)
                                if let fix = check.fix { Text(fix).font(.caption).foregroundStyle(.secondary).textSelection(.enabled) }
                            }
                        }
                        .accessibilityElement(children: .combine)
                    }
                    if let version = status.version { LabeledContent("Server version", value: version) }
                } else {
                    Text(app.isPaired ? (app.lastError ?? "Checking…") : "Pair this Mac to see status.")
                        .foregroundStyle(.secondary)
                }
                Button("Check again") { Task { await app.refresh() } }.disabled(!app.isPaired || app.refreshing)
            }
            Section {
                HStack {
                    Button(app.isPaired ? "Re-pair…" : "Pair…") {
                        (NSApp.delegate as? AppDelegate)?.showOnboarding()
                    }
                    if app.isPaired {
                        Button("Unpair", role: .destructive) { confirmUnpair = true }
                    }
                }
            }
        }
        .formStyle(.grouped)
        .confirmationDialog("Unpair this Mac?", isPresented: $confirmUnpair) {
            Button("Unpair", role: .destructive) { app.unpair() }
        } message: {
            Text("The device token is removed from this Mac's Keychain. Revoke it on the server with hermes voice revoke.")
        }
    }
}

// MARK: About

private struct AboutSettings: View {
    @EnvironmentObject var app: AppModel
    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: "waveform.circle.fill").font(.system(size: 48)).foregroundStyle(.tint)
            Text("Speakeasy").font(.title2.weight(.semibold))
            Text("Version \(app.appVersion)").foregroundStyle(.secondary)
            Text("Talk to your own Hermes agent by voice.").foregroundStyle(.secondary)
            Text("MIT License").font(.callout)
            Text("Uses WebRTC (BSD license).").font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
