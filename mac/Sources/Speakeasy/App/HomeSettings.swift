import AppKit
import SpeakeasyCore
import SwiftUI

/// Fallback copy for older servers that don't send an explainer.
let homeExplainerFallback = "Home control lets your voice run your Home Assistant devices directly, in about a second. Say \"lights off in the kitchen and the den to 70\" and it's done before you finish your sentence. It only uses the devices you tick below. Anything with a time, a condition, or a device it isn't sure about still goes to your agent as a normal task."

/// The device list shared by the setup step and Settings: every device Home Assistant offers,
/// grouped by kind, each with a checkbox. `selection` is the set of entity ids ticked.
struct HomeDevicePicker: View {
    let info: HomeControlInfo
    @Binding var selection: Set<String>
    @State private var filter = ""
    @State private var expanded: Set<String> = []

    private func matches(_ d: HomeControlInfo.Device) -> Bool {
        let q = filter.trimmingCharacters(in: .whitespaces).lowercased()
        return q.isEmpty || d.name.lowercased().contains(q) || d.entityID.contains(q)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                TextField("Filter devices", text: $filter).textFieldStyle(.roundedBorder)
                Text("\(selection.count) of \(info.devices.count) on").font(.caption).foregroundStyle(.secondary)
                    .monospacedDigit()
            }
            List {
                ForEach(info.groups, id: \.kind) { group in
                    let shown = group.devices.filter(matches)
                    if !shown.isEmpty {
                        let ids = Set(group.devices.map(\.entityID))
                        let on = ids.intersection(selection).count
                        DisclosureGroup(isExpanded: Binding(
                            get: { expanded.contains(group.kind) || !filter.isEmpty },
                            set: { if $0 { expanded.insert(group.kind) } else { expanded.remove(group.kind) } })) {
                            ForEach(shown) { device in
                                Toggle(isOn: Binding(
                                    get: { selection.contains(device.entityID) },
                                    set: { if $0 { selection.insert(device.entityID) } else { selection.remove(device.entityID) } })) {
                                    HStack {
                                        Text(device.name).lineLimit(1)
                                        Spacer()
                                        Text(device.state.replacingOccurrences(of: "_", with: " "))
                                            .font(.caption).foregroundStyle(.secondary)
                                    }
                                }
                                .toggleStyle(.checkbox)
                                .help(device.entityID)
                            }
                        } label: {
                            HStack {
                                Text(group.devices.first?.kindLabel ?? group.kind).fontWeight(.medium)
                                Spacer()
                                Text("\(on)/\(group.devices.count)").font(.caption).foregroundStyle(.secondary).monospacedDigit()
                                Button(on == group.devices.count ? "None" : "All") {
                                    if on == group.devices.count { selection.subtract(ids) } else { selection.formUnion(ids) }
                                }
                                .buttonStyle(.borderless).font(.caption)
                                .accessibilityLabel(on == group.devices.count ? "Untick all \(group.devices.first?.kindLabel ?? "")"
                                                                              : "Tick all \(group.devices.first?.kindLabel ?? "")")
                            }
                        }
                    }
                }
            }
            .listStyle(.bordered(alternatesRowBackgrounds: false))
            if info.devices.contains(where: { $0.kind == "lock" }) {
                Text("Locks are always confirmed out loud before anything happens.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }
}

/// Settings › Home.
struct HomeSettings: View {
    @EnvironmentObject var app: AppModel
    @State private var selection: Set<String> = []
    @State private var loaded = false
    @State private var saving = false
    @State private var saved = false

    private var info: HomeControlInfo? { app.home }
    private var dirty: Bool { loaded && info.map { Set($0.includedIDs) != selection } == true }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(info?.explainer.isEmpty == false ? info!.explainer : homeExplainerFallback)
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if let info {
                if info.available {
                    Toggle("Use home control on calls", isOn: Binding(
                        get: { info.enabled },
                        set: { v in Task { saving = true; await app.setHome(enabled: v); saving = false; sync() } }))
                    .disabled(saving)
                    HomeDevicePicker(info: info, selection: $selection)
                        .disabled(!info.enabled)
                        .opacity(info.enabled ? 1 : 0.5)
                } else {
                    Label(info.reason.isEmpty ? "Home Assistant isn't set up in Hermes." : info.reason,
                          systemImage: "house.slash")
                        .foregroundStyle(.secondary)
                    Text("Home control uses the Home Assistant connection your Hermes already has (HASS_URL and HASS_TOKEN in its .env). Set that up in Hermes, then come back here.")
                        .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    Spacer()
                }
            } else if let error = app.homeError {
                Label("Couldn't load home control: \(error)", systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
                Spacer()
            } else {
                ProgressView("Looking for Home Assistant…").frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            HStack {
                if let error = app.homeError, info != nil { Text(error).font(.caption).foregroundStyle(.orange).lineLimit(2) }
                else if saved && !dirty { Text("Saved").font(.caption).foregroundStyle(.secondary) }
                Spacer()
                Button("Refresh") { Task { await app.refreshHome(); sync() } }.disabled(saving)
                Button("Revert") { sync() }.disabled(!dirty || saving)
                Button("Save") {
                    Task {
                        saving = true
                        saved = await app.setHome(entities: Array(selection))
                        saving = false
                        sync()
                    }
                }
                .keyboardShortcut("s", modifiers: .command)
                .disabled(!dirty || saving || info?.enabled != true)
            }
        }
        .padding(20)
        .task { await app.refreshHome(); sync() }
    }

    private func sync() {
        if let info { selection = Set(info.includedIDs); loaded = true }
    }
}

/// Setup step, shown only when Hermes already has Home Assistant.
struct HomeStepContent: View {
    @EnvironmentObject var app: AppModel
    @Binding var selection: Set<String>
    let info: HomeControlInfo

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(info.explainer.isEmpty ? homeExplainerFallback : info.explainer)
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Text("We found your Home Assistant. These are the devices it would use; untick anything you'd rather it didn't touch.")
                .font(.callout).fixedSize(horizontal: false, vertical: true)
            HomeDevicePicker(info: info, selection: $selection)
        }
    }
}


// MARK: Home smoke (development only)

/// `--home-smoke <dir>`: snapshots the Home tab and the setup step with a sample device list
/// (no server or Home Assistant is contacted). Run on a development machine, never a user's.
@MainActor
enum HomeSmoke {
    static let sample = HomeControlInfo(
        enabled: true, configured: true, available: true, reason: "",
        devices: [
            .init(entityID: "light.kitchen", name: "Ceiling Lights", kind: "light", state: "on", included: true),
            .init(entityID: "light.pendants", name: "Pendant Lights", kind: "light", state: "off", included: true),
            .init(entityID: "light.bed_left", name: "Reading lamp", kind: "light", state: "on", included: true),
            .init(entityID: "light.office", name: "Study Lights", kind: "light", state: "off", included: false),
            .init(entityID: "climate.den", name: "Den", kind: "climate", state: "cool", included: true),
            .init(entityID: "climate.bedroom", name: "Bedroom", kind: "climate", state: "cool", included: true),
            .init(entityID: "fan.bedroom", name: "Bedroom Fan", kind: "fan", state: "off", included: true),
            .init(entityID: "switch.kettle", name: "Kettle", kind: "switch", state: "off", included: false),
            .init(entityID: "cover.blinds", name: "Living Room Blinds", kind: "cover", state: "open", included: false),
            .init(entityID: "lock.front", name: "Front Door", kind: "lock", state: "locked", included: false),
        ],
        explainer: homeExplainerFallback, unit: "C")

    static func run(app: AppModel, dir: String) {
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        app.home = sample
        func window<V: View>(_ view: V, _ size: NSSize, _ title: String) -> NSWindow {
            let w = NSWindow(contentViewController: NSHostingController(rootView: view.environmentObject(app)))
            w.title = title; w.setContentSize(size); w.makeKeyAndOrderFront(nil)
            return w
        }
        let settings = window(HomeSettingsPreview().frame(width: 560, height: 470), NSSize(width: 560, height: 470), "Home")
        let step = window(HomeStepPreview().padding(24).frame(width: 520, height: 460), NSSize(width: 520, height: 460), "Setup")
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            for (w, name) in [(settings, "home-settings"), (step, "home-setup")] {
                guard let view = w.contentView, let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { continue }
                view.cacheDisplay(in: view.bounds, to: rep)
                try? rep.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: "\(dir)/\(name).png"))
                print("snapshot \(dir)/\(name).png")
            }
            exit(0)
        }
    }
}

/// The Settings tab body without its network loading, for the smoke snapshot.
private struct HomeSettingsPreview: View {
    @EnvironmentObject var app: AppModel
    @State private var selection: Set<String> = Set(HomeSmoke.sample.includedIDs)
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(homeExplainerFallback).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Toggle("Use home control on calls", isOn: .constant(true))
            HomeDevicePicker(info: HomeSmoke.sample, selection: $selection)
            HStack { Spacer(); Button("Refresh") {}; Button("Revert") {}.disabled(true); Button("Save") {}.disabled(true) }
        }
        .padding(20)
    }
}

private struct HomeStepPreview: View {
    @State private var selection: Set<String> = Set(HomeSmoke.sample.includedIDs)
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Home control").font(.title2.bold())
            Text("Control your home by voice, instantly.").foregroundStyle(.secondary)
            HomeStepContent(selection: $selection, info: HomeSmoke.sample).frame(maxHeight: .infinity)
            HStack { Button("Back") {}; Spacer(); Button("Not now") {}; Button("Turn on") {}.keyboardShortcut(.defaultAction) }
            Text("You can change this any time in Settings › Home.").font(.caption).foregroundStyle(.secondary)
        }
    }
}
