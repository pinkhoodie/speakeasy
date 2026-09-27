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
            ShortcutSettings().tabItem { Label("Shortcuts", systemImage: "keyboard") }
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
    @AppStorage(Prefs.showCaptions) private var showCaptions = true
    @AppStorage(Prefs.notifyWhenDone) private var notifyWhenDone = true
    @AppStorage(Prefs.panelOnAllSpaces) private var panelOnAllSpaces = true

    var body: some View {
        Form {
            Section("Calls") {
                Toggle("Start calls muted", isOn: $startMuted)
                Toggle("Follow the system's audio devices (AirPods etc.)", isOn: $followSystemAudio)
            }
            Section("Panel") {
                Toggle("Show the panel when a call starts", isOn: $showPanelOnStart)
                Toggle("Start calls in slim mode", isOn: $startSlim)
                    .help("Just the controls and a one-line task summary; expand any time")
                Toggle("Show live captions", isOn: $showCaptions)
                    .help("What you and the assistant say, as text in the panel")
                Toggle("Keep the panel on every desktop", isOn: $panelOnAllSpaces)
                    .help("Off: the panel stays on the desktop (Space) where the call started")
            }
            Section("After a call") {
                Toggle("Notify me when work from a call finishes", isOn: $notifyWhenDone)
                    .help("A macOS notification when a task you started keeps running after you hang up and then finishes")
            }
            Section {
                Toggle("Launch at login", isOn: Binding(get: { app.launchAtLogin }, set: { app.launchAtLogin = $0 }))
            }
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

// MARK: Shortcuts

private struct ShortcutSettings: View {
    @EnvironmentObject var app: AppModel

    private var mute: KeyShortcut? { AppModel.storedShortcut(Prefs.muteShortcut, default: .defaultMute) }
    private var pause: KeyShortcut? { AppModel.storedShortcut(Prefs.pauseShortcut, default: .defaultPause) }

    var body: some View {
        Form {
            Section {
                LabeledContent("Start or end a call") {
                    ShortcutRecorder(shortcut: app.callShortcut, taken: [mute, pause].compactMap { $0 }) { app.setCallShortcut($0) }
                }
                if let problem = app.callShortcutProblem { Text(problem).foregroundStyle(.orange) }
            } footer: {
                Text("Works from any app. Press it again to show the panel, or to end the call when the panel is showing.")
            }
            Section {
                LabeledContent("Mute or unmute") {
                    ShortcutRecorder(shortcut: mute, name: "Mute", defaultShortcut: .defaultMute,
                                     taken: [app.callShortcut] + [pause].compactMap { $0 },
                                     onTurnOff: { app.setExtraShortcut(Prefs.muteShortcut, nil) }) {
                        app.setExtraShortcut(Prefs.muteShortcut, $0)
                    }
                }
                if let problem = app.muteShortcutProblem, mute != nil { Text(problem).foregroundStyle(.orange) }
            } footer: {
                Text("Tap to mute or unmute. Hold while muted to talk, hold while live to cough. Only active during a call.")
            }
            Section {
                LabeledContent("Pause or resume") {
                    ShortcutRecorder(shortcut: pause, name: "Pause", defaultShortcut: .defaultPause,
                                     taken: [app.callShortcut] + [mute].compactMap { $0 },
                                     onTurnOff: { app.setExtraShortcut(Prefs.pauseShortcut, nil) }) {
                        app.setExtraShortcut(Prefs.pauseShortcut, $0)
                    }
                }
                if let problem = app.pauseShortcutProblem, pause != nil { Text(problem).foregroundStyle(.orange) }
            } footer: {
                Text("A paused call stops listening and billing; tasks keep running.")
            }
            Section {
                Button("Restore default shortcuts") { app.resetShortcuts() }
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

/// One-line explanation shared by Settings and onboarding.
let continuityHelp = "When you ask about something you were already discussing in a Hermes chat or thread, the task continues inside that conversation, with its history, and the reply posts there."

private struct BehaviorSettings: View {
    @EnvironmentObject var app: AppModel
    @State private var draft = ServerSettings()

    var body: some View {
        ServerForm(draft: $draft) {
            Section {
                Text("Several requests can run at once. Each becomes its own task in the panel; follow-ups go to the right one.")
                    .font(.callout).foregroundStyle(.secondary)
            }
            VStack(alignment: .leading, spacing: 4) {
                Toggle("Continue existing conversations", isOn: Binding(
                    get: { draft.continuity?.enabled ?? true },
                    set: { v in var c = draft.continuity ?? .init(); c.enabled = v; draft.continuity = c }))
                Text(continuityHelp).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            Section("Spoken updates") {
                Toggle("Say a quick “on it” when a task starts", isOn: Binding(
                    get: { draft.speech?.acknowledge ?? true },
                    set: { v in var x = draft.speech ?? .init(); x.acknowledge = v; draft.speech = x }))
                Toggle("Give a short update on long tasks", isOn: Binding(
                    get: { draft.speech?.progress ?? true },
                    set: { v in var x = draft.speech ?? .init(); x.progress = v; draft.speech = x }))
                Text("Where a task went (like a new thread) is always said.").font(.caption).foregroundStyle(.secondary)
            }
            Stepper(value: Binding(get: { Int(draft.idlePauseMinutes ?? 5) }, set: { draft.idlePauseMinutes = Double($0) }),
                    in: 0...60) {
                let minutes = Int(draft.idlePauseMinutes ?? 5)
                Text(minutes == 0 ? "Auto-pause a quiet call: off" : "Auto-pause a quiet call after \(minutes) min")
            }
            Stepper(value: Binding(get: { draft.maxCallMinutes ?? 30 }, set: { draft.maxCallMinutes = $0 }),
                    in: 5...240, step: 5) {
                Text("End a call after \(draft.maxCallMinutes ?? 30) min")
            }
            .help("A hard cap per call so a forgotten call can't run up your plan or bill")
            Section("Task routing") {
                LabeledContent("Routing model", value: app.status?.routingModel ?? "Unknown")
                Text(app.status?.routingHint ?? "Change it in your Hermes config under auxiliary → speakeasy_router.")
                    .font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

// MARK: Delivery

private struct DeliverySettings: View {
    @EnvironmentObject var app: AppModel
    @State private var draft = ServerSettings()
    @State private var adding = false
    @State private var suggesting = false
    @State private var suggestions: [ServerSettings.Channel]?
    @State private var suggestError: String?

    private var channels: [ServerSettings.Channel] { draft.delivery?.channels ?? [] }

    private func setChannels(_ list: [ServerSettings.Channel]) {
        var d = draft.delivery ?? .init(); d.channels = list; draft.delivery = d
    }

    var body: some View {
        ServerForm(draft: $draft) {
            Section {
                Text("Where finished work goes by default, and where tasks go that don't fit a channel below.")
                    .font(.callout).foregroundStyle(.secondary)
                DeliveryPicker(target: Binding(get: { draft.deliveryTarget }, set: { t in
                    var d = draft.delivery ?? .init(); d.target = t; if t == nil { d.newThread = false }
                    draft.delivery = d
                    draft.notifyTarget = t
                }), destinations: app.destinations)
                if app.status?.canOpenThread(in: draft.deliveryTarget) == true {
                    Toggle("Run each task in a new thread there", isOn: Binding(
                        get: { draft.delivery?.newThread ?? false },
                        set: { v in var d = draft.delivery ?? .init(); d.newThread = v; draft.delivery = d }))
                        .help("You can follow up in that thread, and the call still hears the result.")
                }
            }
            Section {
                ForEach(channels) { channel in
                    ChannelRow(channel: Binding(
                        get: { channel },
                        set: { new in setChannels(channels.map { $0.target == channel.target ? new : $0 }) }),
                        destinationLabel: app.destinations.first(where: { $0.target == channel.target })?.label ?? channel.target,
                        canThread: app.status?.canOpenThread(in: channel.target) == true,
                        remove: { setChannels(channels.filter { $0.target != channel.target }) })
                }
                HStack {
                    Button("Add channel…") { adding = true }.disabled(channels.count >= 8 || app.destinations.isEmpty)
                    Button {
                        suggest()
                    } label: {
                        if suggesting { ProgressView().controlSize(.small) } else { Text("Suggest channels") }
                    }
                    .disabled(suggesting || app.destinations.isEmpty)
                    .help("Your Hermes proposes channels from what it knows about your work. Nothing is saved until you pick.")
                }
                if let suggestError { Text(suggestError).font(.caption).foregroundStyle(.orange) }
            } header: {
                Text("More channels")
            } footer: {
                Text("A new task goes to the channel you name (\"start this in #build\"), else the one whose topic fits, else the default. Follow-ups stay where their task runs.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if app.destinations.isEmpty {
                Text("No connected Hermes chats found. Connect one in Hermes (e.g. Telegram), then reopen Settings.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .sheet(isPresented: $adding) {
            AddChannelSheet(destinations: app.destinations.filter { d in !channels.contains { $0.target == d.target } },
                            canThread: { app.status?.canOpenThread(in: $0) == true }) { new in
                setChannels(ChannelSuggestions.merge([new], into: channels))
            }
        }
        .sheet(item: Binding(get: { suggestions.map { SuggestionList(items: $0) } }, set: { suggestions = $0?.items })) { list in
            SuggestionsSheet(items: list.items, existing: channels,
                             canThread: { app.status?.canOpenThread(in: $0) == true }) { picked in
                setChannels(ChannelSuggestions.merge(picked, into: channels))
            }
        }
    }

    private func suggest() {
        suggesting = true; suggestError = nil
        Task {
            let result = await app.suggestChannels()
            suggesting = false
            if let result, !result.isEmpty { suggestions = result }
            else { suggestError = app.lastError ?? "Hermes didn't suggest any channels." }
        }
    }
}

private struct SuggestionList: Identifiable {
    let items: [ServerSettings.Channel]
    var id: String { items.map(\.target).joined(separator: ",") }
}

private struct ChannelRow: View {
    @Binding var channel: ServerSettings.Channel
    var destinationLabel: String
    var canThread: Bool
    var remove: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                TextField("Name", text: $channel.label, prompt: Text("#build")).frame(maxWidth: 140)
                Text(destinationLabel).font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                    .help(destinationLabel)
                Spacer()
                Button(role: .destructive, action: remove) { Image(systemName: "minus.circle") }
                    .buttonStyle(.borderless).help("Remove this channel").accessibilityLabel("Remove \(channel.label)")
            }
            TextField("Topic", text: $channel.topic, prompt: Text("e.g. building or changing software, apps, agents"))
            if canThread {
                Toggle("Run each task in a new thread", isOn: $channel.newThread)
            }
        }
        .padding(.vertical, 2)
    }
}

private struct AddChannelSheet: View {
    var destinations: [Destination]
    var canThread: (String) -> Bool
    var add: (ServerSettings.Channel) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var target: String?
    @State private var label = ""
    @State private var topic = ""
    @State private var newThread = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Add a channel").font(.headline)
            Form {
                Picker("Chat", selection: $target) {
                    Text("Choose…").tag(String?.none)
                    ForEach(destinations) { d in Text(d.label).tag(String?.some(d.target)) }
                }
                TextField("Name", text: $label, prompt: Text("#build"))
                TextField("Topic", text: $topic, prompt: Text("building or changing software, apps, agents"))
                if let target, canThread(target) { Toggle("Run each task in a new thread", isOn: $newThread) }
            }
            .formStyle(.grouped)
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                Button("Add") {
                    if let target {
                        add(.init(target: target, label: label.trimmingCharacters(in: .whitespaces), topic: topic,
                                  newThread: newThread && canThread(target)))
                    }
                    dismiss()
                }
                .keyboardShortcut(.defaultAction)
                .disabled(target == nil || label.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(20).frame(width: 440)
        .onChange(of: target) { _, new in
            if label.isEmpty, let d = destinations.first(where: { $0.target == new }) { label = suggestedChannelLabel(d.label) }
        }
    }
}

/// The channels Hermes suggested, with checkboxes; only the picked ones are added (then Save).
struct SuggestionsSheet: View {
    var items: [ServerSettings.Channel]
    var existing: [ServerSettings.Channel]
    var canThread: (String) -> Bool
    var add: ([ServerSettings.Channel]) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var picked: Set<String> = []

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Suggested channels").font(.headline)
            Text("From what your Hermes knows about your work. Pick the ones you want; nothing is saved until you press Save.")
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            ForEach(items) { item in
                let already = existing.contains { $0.target == item.target }
                Toggle(isOn: Binding(get: { picked.contains(item.target) },
                                     set: { on in if on { picked.insert(item.target) } else { picked.remove(item.target) } })) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(item.label + (item.newThread && canThread(item.target) ? " · new thread per task" : "")).fontWeight(.medium)
                        Text(item.topic).font(.caption).foregroundStyle(.secondary)
                        if already { Text("Already added").font(.caption2).foregroundStyle(.secondary) }
                    }
                }
                .toggleStyle(.checkbox)
                .disabled(already)
            }
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                Button("Add selected") {
                    add(items.filter { picked.contains($0.target) }.map { c in
                        var c = c; c.newThread = c.newThread && canThread(c.target); return c
                    })
                    dismiss()
                }
                .keyboardShortcut(.defaultAction)
                .disabled(picked.isEmpty)
            }
        }
        .padding(20).frame(width: 460)
        .onAppear { picked = Set(items.filter { i in !existing.contains { $0.target == i.target } }.map(\.target)) }
    }
}

// MARK: Connection

private struct ConnectionSettings: View {
    @EnvironmentObject var app: AppModel
    @State private var confirmUnpair = false

    var body: some View {
        Form {
            LabeledContent("Server", value: app.config.serverURL?.absoluteString ?? "—")
            if let status = app.status {
                Label(status.reachability, systemImage: status.tailscaleName?.isEmpty == false ? "lock.shield.fill" : "desktopcomputer")
                    .font(.headline)
            }
            if let offer = app.tailnetOffer {
                TailnetOffer(url: offer)
            }
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

/// Shown when this Mac is paired to the server's loopback address but can't reach it, while the
/// server advertised a tailnet address: offer to switch to it.
struct TailnetOffer: View {
    @EnvironmentObject var app: AppModel
    var url: URL
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("Can't reach the server on this Mac, but it's available over Tailscale.", systemImage: "exclamationmark.triangle.fill")
                .foregroundStyle(.orange)
            Button("Switch to \(url.host ?? url.absoluteString)") { Task { await app.switchServer(to: url) } }
        }
    }
}

/// "#build" from a destination label like "Discord · Home / build".
func suggestedChannelLabel(_ destination: String) -> String {
    let last = destination.split(whereSeparator: { $0 == "·" || $0 == "/" }).last.map { $0.trimmingCharacters(in: .whitespaces) } ?? destination
    let name = last.hasPrefix("#") ? String(last.dropFirst()) : last
    return "#" + String(name.prefix(39))
}
