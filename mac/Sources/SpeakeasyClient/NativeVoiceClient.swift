#if os(macOS)
import AppKit
#endif
import AVFoundation
import Foundation
import SpeakeasyCore

/// The call surface the app delegate drives.
@MainActor
public protocol VoiceCallClient: AnyObject {
    var onStatus: ((String) -> Void)? { get set }
    var onError: ((String) -> Void)? { get set }
    var onClosed: (() -> Void)? { get set }
    /// Open the next call with the microphone muted (push-to-talk style).
    var startMuted: Bool { get set }
    /// True while a call is connecting/live, i.e. mute is meaningful.
    var micControllable: Bool { get }
    var micMuted: Bool { get }
    /// Local capture track only: never closes the call, never touches Hermes work.
    func setMicMuted(_ muted: Bool)
    func start()
    func end()
    func showWork(active: Bool)
    func hideIfIdle()
    /// Pause closes the billed voice session but keeps the conversation and its
    /// tasks; Resume continues it in a new session. False when unsupported.
    var supportsPause: Bool { get }
    var isPaused: Bool { get }
    func togglePause()
}

/// What the call client needs from the platform UI that shows it.
@MainActor
public protocol VoiceSurface: AnyObject {
    var isVisible: Bool { get }
    var onEscape: (() -> Void)? { get set }
    /// Mac: follow across Spaces. Ignored where it means nothing (iPhone).
    var onAllSpaces: Bool { get set }
    func show()
    func hide()
    func focus()
    func setNeedsResize()
}

/// Native call client: WebRTC transport + server SSE + pure reducer + SwiftUI panel.
@MainActor
public final class NativeVoiceClient: VoiceCallClient {
    public var onStatus: ((String) -> Void)?
    public var onError: ((String) -> Void)?
    public var onClosed: (() -> Void)?
    /// Paused (true) or resumed (false): the conversation stays open either way.
    public var onPauseChanged: ((Bool) -> Void)?
    /// A task settled while the call was paused (heads-up with a Resume action).
    public var onPausedTaskSettled: ((WorkNotice) -> Void)?
    public var startMuted = false
    /// Client preferences (Settings › General).
    public var showPanelOnStart = true
    /// Settings › General "Start calls in slim mode".
    public var startSlim = false
    public var followSystemAudio = true
    public var panelOnAllSpaces = true { didSet { surface.onAllSpaces = panelOnAllSpaces } }
    public var supportsPause: Bool { true }
    public var isPaused: Bool { model.state.connection == .paused }

    public var micControllable: Bool { model.state.connection == .live || model.state.connection == .connecting }
    public var micMuted: Bool { model.state.mic == .muted }
    public func setMicMuted(_ muted: Bool) {
        guard micControllable else { return }
        dispatch(.setMic(muted ? .muted : .live))
    }

    public private(set) var config: AppConfig
    public private(set) var api: ServerClient?
    /// Set by the app before `start()`: the next new call runs the first-call tour, naming these
    /// shortcuts. Consumed once the server admits the call (`onTourStarted`).
    public var pendingTour: [String: String]?
    public var onTourStarted: (() -> Void)?
    private var autoHide = PanelAutoHide()
    private var autoHideTimer: Timer?
    /// Fires when the panel hides itself or is closed (the app updates its menu).
    public var onPanelHidden: (() -> Void)?
    public let model = VoicePanelModel()
    /// The platform surface (Mac floating panel, iPhone call screen) the client shows and resizes.
    public private(set) lazy var surface: any VoiceSurface = makeSurface(model)
    private let makeSurface: @MainActor (VoicePanelModel) -> any VoiceSurface

    private var engine: NativeCallEngine?
    private var startTask: Task<Void, Never>?
    private var streamTask: Task<Void, Never>?
    private var pollTask: Task<Void, Never>?
    private var workOnlyTask: Task<Void, Never>?
    private var ticker: Timer?
    private var levelTimer: Timer?
    private var endDeadline: DispatchWorkItem?
    private var dwell = StatusDwell(minimumDwell: 1.5)
    private var closeNotified = false
    private var voiceProvider = "openai"
    private var codexStopRequested = false
    private var appliedMic: MicState?
    private var appliedRemote: Bool?
    /// Preview mode: canned state, no network, no audio.
    public private(set) var previewMode = false

    public init(config: AppConfig, makeSurface: @escaping @MainActor (VoicePanelModel) -> any VoiceSurface) {
        self.config = config
        self.makeSurface = makeSurface
        self.api = ServerClient(config: config)
        model.onClosePanel = { [weak self] in self?.closePanel() }
        model.onDraftAction = { [weak self] draft, action, text in self?.draftAction(draft, action, instructions: text) }
        model.onToggleMic = { [weak self] in self?.dispatch(.toggleMic) }
        model.onStart = { [weak self] in self?.start() }
        model.onEnd = { [weak self] in self?.end() }
        model.onToggleSlim = { [weak self] in
            guard let self else { return }
            self.model.slim.toggle()
            if self.model.slim { self.model.workExpanded = false; self.model.selectedTaskID = nil }
            self.surface.setNeedsResize()
        }
        model.onApproval = { [weak self] choice in self?.resolveApproval(choice) }
        model.onStopWork = { [weak self] in self?.stopWork() }
        model.onToggleWork = { [weak self] in self?.toggleWorkView() }
        model.onCloseWork = { [weak self] in self?.closeWorkView() }
        model.onTogglePause = { [weak self] in self?.togglePause() }
        model.onStopTask = { [weak self] runID in self?.stopWork(runID: runID) }
        model.onSelectTask = { [weak self] id in self?.selectTask(id) }
        model.onDismissTasks = { [weak self] runIDs in self?.dismissTasks(runIDs) }
        model.onDismissReview = { [weak self] runID in self?.dismissReview(runID) }
        model.loadProductImage = { [weak self] runID, index in
            guard let self, let api = self.api else { return nil }
            return try? await api.image(runID: runID, index: index)
        }
        model.loadLiveImage = { [weak self] runID in
            guard let self, let api = self.api else { return nil }
            return try? await api.liveImage(runID: runID)
        }
    }

    // MARK: Reducer plumbing

    public func dispatch(_ event: VoiceEvent) {
        let before = model.state
        let after = reduce(before, event, now: Date())
        model.state = after
        applySideEffects(from: before, to: after)
    }

    private func applySideEffects(from old: VoiceState, to new: VoiceState) {
        if appliedMic != new.mic {
            appliedMic = new.mic
            engine?.setMicEnabled(new.localAudioEnabled)
        }
        if appliedRemote != new.remoteAudioEnabled {
            appliedRemote = new.remoteAudioEnabled
            engine?.setRemoteAudioEnabled(new.remoteAudioEnabled)
        }
        refreshStatusLine()
        if let show = new.showRequest, show != old.showRequest { act(on: show) }
        if old.exchange.isEmpty != new.exchange.isEmpty || old.approval != new.approval ||
            old.connection != new.connection || old.workInfo != new.workInfo || old.tasks != new.tasks {
            surface.setNeedsResize()
        }
        if new.connection == .paused, old.connection != .paused { callPaused() }
        if old.exchange != new.exchange { surface.setNeedsResize() }
        if case .ended(let fin) = new.connection, old.connection != new.connection { finishCall(fin) }
        if case .failed(let message) = new.connection, old.connection != new.connection {
            onError?(message)
            finishCall(.incomplete)
        }
        if let error = new.lastError, error != old.lastError { onError?(error) }
        if old.connection != new.connection { onStatus?(present(new).primary + " · " + present(new).secondary) }
    }

    private func refreshStatusLine() {
        let p = model.presentation
        let urgent = p.tone == .attention || p.tone == .error || model.shownStatus.isEmpty
        if dwell.offer(p.secondary, urgent: urgent, now: Date()) {
            model.shownStatus = dwell.shown ?? ""
            model.shownTone = p.tone
        } else if dwell.shown == p.secondary, model.shownTone != p.tone {
            model.shownTone = p.tone   // same words, tone aged (e.g. glimmer → plain)
        }
    }

    private func startTicker() {
        guard ticker == nil else { return }
        ticker = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self else { return }
                if previewMode { return }
                dispatch(.tick)
                // Five quiet minutes: close the paid voice session. Pause, not End, so the
                // conversation and running tasks survive and Resume picks up where it left off.
                if model.state.idleExpired(at: Date()) {
                    let minutes = Int((model.state.idleTimeout / 60).rounded())
                    onStatus?("No audio for \(minutes) minute\(minutes == 1 ? "" : "s"): voice paused")
                    pause()
                }
                if dwell.tick(now: Date()) {
                    model.shownStatus = dwell.shown ?? ""
                    model.shownTone = model.presentation.tone
                }
            }
        }
    }

    private func stopTicker() {
        ticker?.invalidate(); ticker = nil
        levelTimer?.invalidate(); levelTimer = nil
        model.orbLevel = 0
    }

    /// ~12 Hz: the orb follows the assistant's voice while it speaks, the mic while listening.
    private func startLevelMeter() {
        guard levelTimer == nil else { return }
        levelTimer = Timer.scheduledTimer(withTimeInterval: 0.08, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, let engine = self.engine, self.model.state.connection == .live,
                      self.surface.isVisible else {
                    if self?.model.orbLevel != 0 { self?.model.orbLevel = 0 }
                    return
                }
                engine.audioLevels { [weak self] mic, voice in
                    guard let self else { return }
                    let speaking = self.model.presentation.mark == .speaking
                    let muted = self.model.state.mic == .muted
                    // Levels are linear; a square root makes quiet speech visible.
                    let raw = speaking ? voice : (muted ? 0 : mic)
                    let target = min(1, sqrt(max(0, raw)) * 1.4)
                    let current = self.model.orbLevel
                    // Fast attack, slower release, so it moves with syllables without flicker.
                    let next = target > current ? current + (target - current) * 0.6 : current + (target - current) * 0.25
                    if abs(next - current) > 0.01 { self.model.orbLevel = next }
                }
            }
        }
    }

    // MARK: Call lifecycle

    public func start() {
        if model.state.connection == .paused { resume(); return }
        guard !model.state.connection.isInCall else { surface.show(); return }
        cancelAutoHide()
        closeNotified = false
        voiceProvider = "openai"
        codexStopRequested = false
        appliedMic = nil; appliedRemote = nil
        dwell = StatusDwell(minimumDwell: 1.5)
        model.workExpanded = false
        model.captionExpanded = false
        workOnlyTask?.cancel()
        dispatch(.startRequested)
        if startMuted { dispatch(.setMic(.muted)) }
        surface.onEscape = { [weak self] in self?.handleEscape() }
        if startSlim { model.slim = true }
        if showPanelOnStart { surface.show() }
        startTicker()
        startLevelMeter()
        #if os(macOS)
        if followSystemAudio { watchAudioDevices() }
        #endif
        guard let api else {
            dispatch(.failed(config.deviceToken == nil ? "Not connected to Hermes — open Settings › Connection to pair"
                             : "The server address isn't trusted — pair again in Settings › Connection"))
            return
        }
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            connect(api)
        case .notDetermined:
            onStatus?("Allow Speakeasy to use the microphone…")
            AVCaptureDevice.requestAccess(for: .audio) { [weak self] granted in
                Task { @MainActor [weak self] in
                    guard let self, self.model.state.connection == .connecting else { return }
                    if granted { self.connect(api) }
                    else { self.dispatch(.failed("Microphone access is off")) }
                }
            }
        default:
            #if os(macOS)
            dispatch(.failed("Microphone access is off — enable Speakeasy in System Settings › Privacy & Security › Microphone"))
            #else
            dispatch(.failed("Microphone access is off — enable it in Settings › Speakeasy › Microphone"))
            #endif
        }
    }

    private func connect(_ api: ServerClient) {
        let engine = NativeCallEngine()
        self.engine = engine
        engine.onMessage = { [weak self] data in
            guard let self else { return }
            for event in parseDataChannelMessage(data) {
                if case .sessionClosed = event, self.voiceProvider == "codex" {
                    self.finishCodexCall()
                } else {
                    self.dispatch(event)
                }
            }
        }
        engine.onChannelClosed = { [weak self] in self?.channelClosed() }
        engine.onAudioFormatChanged = { [weak self] in self?.audioDevicesChanged() }
        engine.onConnectionFailed = { [weak self] in
            guard let self, self.model.state.connection == .live || self.model.state.connection == .connecting else { return }
            self.dispatch(.transportLost)
        }
        startTask = Task { [weak self] in
            do {
                await engine.warmAudioRoute()
                try engine.prepare(captureAudio: true)
                // Honour a mute chosen before/while connecting (start-muted or hotkey).
                engine.setMicEnabled(self?.model.state.localAudioEnabled ?? false)
                let sdp = try await engine.createOffer()
                guard let self, self.engine === engine, self.model.state.connection == .connecting else { engine.close(); return }
                let tour = self.model.state.resumeFrom == nil ? self.pendingTour : nil
                let admission = try await api.admitSession(sdp: sdp, idempotencyKey: UUID().uuidString,
                                                              resumeFrom: self.model.state.resumeFrom, tour: tour)
                guard self.engine === engine else { engine.close(); return }
                if tour != nil {
                    self.pendingTour = nil
                    self.model.tourActive = true
                    self.onTourStarted?()
                }
                self.voiceProvider = admission.voiceProvider
                self.codexStopRequested = false
                self.dispatch(.sessionAdmitted(interactionID: admission.interactionID))
                try await engine.setRemoteAnswer(admission.answerSDP)
                self.appliedMic = nil; self.appliedRemote = nil
                self.dispatch(.tick)
                self.startServerEvents(api, interactionID: admission.interactionID)
                if self.model.state.connection == .ending { self.sendClose() }
            } catch {
                guard let self, self.engine === engine else { return }
                // The api no longer holds the paused call (restarted, or it was
                // already resumed): start a fresh call rather than failing.
                if self.model.state.resumeFrom != nil, let http = error as? ServerClient.HTTPError,
                   http.status == 404 || http.status == 409, self.model.state.connection == .connecting {
                    engine.close()
                    self.model.state.resumeFrom = nil
                    self.dispatch(.error("Couldn't resume the paused call; started a new one"))
                    self.connect(api)
                    return
                }
                self.dispatch(.failed(error.localizedDescription))
            }
        }
    }

    public func end() {
        switch model.state.connection {
        case .paused:
            dispatch(.endRequested)
        case .connecting, .live:
            dispatch(.endRequested)
            if model.state.connection == .ending { sendClose() }
            else { startTask?.cancel() }
        case .ending:
            break
        default:
            if model.state.workOnly || model.workExpanded { closeWorkView() } else { hideNow() }
        }
    }

    // MARK: Pause / Resume

    public func togglePause() {
        switch model.state.connection {
        case .paused: resume()
        case .live: pause()
        default: break
        }
    }

    /// Ask the api to hold the conversation, then close the voice session.
    /// If the api cannot hold it the call stays live (nothing is lost).
    private func pause() {
        guard model.state.canPause, let api, let id = model.state.interactionID else { return }
        dispatch(.pauseRequested)
        Task { [weak self] in
            do {
                try await api.pause(interactionID: id)
                guard let self, self.model.state.pausing else { return }
                self.sendClose()
            } catch {
                self?.reconnectAfterPause = false
                self?.dispatch(.pauseFailed("Couldn't pause: \(error.localizedDescription)"))
            }
        }
    }

    private func callPaused() {
        endDeadline?.cancel(); endDeadline = nil
        startTask?.cancel(); startTask = nil
        engine?.close(); engine = nil
        streamTask?.cancel(); streamTask = nil
        pollTask?.cancel(); pollTask = nil
        if reconnectAfterPause {
            // Audio device switch: reopen straight away on the new device.
            reconnectAfterPause = false
            resume()
            return
        }
        startPausedTaskPolling()
        onPauseChanged?(true)
    }

    // MARK: Audio device changes (macOS)

    /// Set while a device switch is being handled as pause → immediate resume.
    private var reconnectAfterPause = false
    #if os(macOS)
    private var deviceWatcher: DefaultAudioDeviceWatcher?

    private func watchAudioDevices() {
        guard deviceWatcher == nil else { return }
        deviceWatcher = DefaultAudioDeviceWatcher { [weak self] in self?.audioDevicesChanged() }
    }

    /// WebRTC keeps playing to the device it opened. When the Mac's default output
    /// or input changes mid-call (AirPods in or out), reconnect the voice session on
    /// the new device. The conversation and tasks are kept; only the audio reopens.
    private func audioDevicesChanged() {
        guard model.state.canPause, !reconnectAfterPause else { return }
        onStatus?("Switching audio to \(DefaultAudioDeviceWatcher.outputName() ?? "the new device")…")
        reconnectAfterPause = true
        pause()
    }
    #else
    /// iOS: AVAudioSession moves WebRTC to AirPods / the speaker itself; nothing to reopen.
    private func audioDevicesChanged() {}
    #endif

    private func resume() {
        guard model.state.connection == .paused, let api else { return }
        pollTask?.cancel(); pollTask = nil
        appliedMic = nil; appliedRemote = nil
        dispatch(.resumeRequested)
        surface.show()
        startTicker()
        startLevelMeter()
        onPauseChanged?(false)
        connect(api)
    }

    /// While paused there is no event stream: keep the task list fresh by
    /// polling the tasks' work rows (no voice session is open, nothing billed).
    private func startPausedTaskPolling() {
        guard let api else { return }
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 3_000_000_000)
                guard let self, self.model.state.connection == .paused else { return }
                var tasks = self.model.state.tasks
                for (index, task) in tasks.enumerated() where task.isActive {
                    if let run = task.info.runID, let info = try? await api.work(runID: run) {
                        tasks[index].info = info
                    }
                }
                guard self.model.state.connection == .paused else { return }
                if tasks != self.model.state.tasks {
                    for notice in PausedTaskNotices.settled(before: self.model.state.tasks, after: tasks) {
                        self.onPausedTaskSettled?(notice)
                    }
                    self.dispatch(.tasks(tasks))
                }
                if let run = self.model.state.runID, let info = tasks.first(where: { $0.info.runID == run })?.info {
                    self.dispatch(.work(info))
                }
            }
        }
    }

    private func sendClose() {
        guard model.state.interactionID != nil else { return }
        endDeadline?.cancel()
        let deadline = DispatchWorkItem { [weak self] in self?.dispatch(.endTimedOut) }
        endDeadline = deadline
        DispatchQueue.main.asyncAfter(deadline: .now() + 8, execute: deadline)
        if engine?.send(json: ["type": "session.close"]) != true {
            engine?.close()
            startFinalizationPolling()
        }
    }

    private func finishCodexCall() {
        guard !codexStopRequested, let api, let id = model.state.interactionID else { return }
        codexStopRequested = true
        // Provider session.closed is not Codex's app-server close. Wait for its
        // stop RPC and the api's finalization before showing a clean end.
        Task { [weak self] in
            do {
                try await api.finishCodexTransport(interactionID: id)
                guard let self, self.model.state.interactionID == id else { return }
                if let snapshot = try await api.interaction(id) {
                    self.dispatch(.interaction(snapshot))
                }
            } catch {
                guard let self, self.model.state.interactionID == id else { return }
                self.dispatch(.error("Codex call end could not be confirmed"))
                self.startFinalizationPolling()
            }
        }
    }

    private func channelClosed() {
        switch model.state.connection {
        case .ending:
            if voiceProvider == "codex" { finishCodexCall() }
            engine?.close()
            startFinalizationPolling()
        case .live, .connecting:
            dispatch(.transportLost)
        default: break
        }
    }

    private func startFinalizationPolling() {
        guard let api, let id = model.state.interactionID else { return }
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                if let snapshot = try? await api.interaction(id) { self?.dispatch(.interaction(snapshot)) }
                guard let self, self.model.state.connection == .ending else { return }
                try? await Task.sleep(nanoseconds: 500_000_000)
            }
        }
    }

    public func skipTour() {
        guard model.tourActive else { return }
        model.tourActive = false
        guard let api, let id = model.state.interactionID else { return }
        Task { try? await api.skipTour(interactionID: id) }
    }

    private func finishCall(_ finalization: Finalization) {
        model.selectedTaskID = nil
        model.tourActive = false
        endDeadline?.cancel(); endDeadline = nil
        startTask?.cancel(); startTask = nil
        engine?.close(); engine = nil
        streamTask?.cancel(); streamTask = nil
        pollTask?.cancel(); pollTask = nil
        let delay: TimeInterval = finalization == .complete ? 1.0 : 2.4
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self, !self.model.state.connection.isOpen else { return }
            // Show the finished state briefly, then hide (hover or anything that
            // needs the user holds it). The x button and Esc hide it at once.
            if self.surface.isVisible && !self.model.workExpanded { self.armAutoHide() }
            if !self.closeNotified { self.closeNotified = true; self.onClosed?() }
        }
    }

    private func hideNow() {
        cancelAutoHide()
        surface.hide()
        if !model.state.connection.isOpen && !model.state.workOnly && !model.workExpanded { stopTicker() }
        onPanelHidden?()
    }

    public var panelVisible: Bool { surface.isVisible }

    /// The x button: idle → hide (leaving any work-only view); in a call → hide only.
    public func closePanel() {
        if model.state.workOnly { workOnlyTask?.cancel(); workOnlyTask = nil; dispatch(.reset) }
        model.selectedTaskID = nil
        model.workExpanded = false
        hideNow()
    }

    public func showPanel() {
        cancelAutoHide()
        surface.onEscape = { [weak self] in self?.handleEscape() }
        surface.show()
    }

    private func armAutoHide() {
        autoHide.arm(now: Date())
        autoHideTimer?.invalidate()
        autoHideTimer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self else { return }
                if self.model.state.connection.isOpen || !self.surface.isVisible { self.cancelAutoHide(); return }
                if self.autoHide.shouldHide(now: Date(), hovering: self.model.hovering,
                                            needsAttention: panelNeedsAttention(self.model.state) || self.model.workExpanded) {
                    self.hideNow()
                }
            }
        }
    }

    private func cancelAutoHide() {
        autoHide.cancel()
        autoHideTimer?.invalidate(); autoHideTimer = nil
    }

    /// Pairing changed or settings reloaded.
    public func update(config: AppConfig) {
        self.config = config
        api = ServerClient(config: config)
    }

    public func apply(settings: ServerSettings) {
        model.state.assistantName = settings.resolvedAssistantName
        model.state.idleTimeout = settings.idleTimeout
    }

    public func setCallShortcutHint(_ hint: String) { model.state.callShortcutHint = hint }

    // MARK: Email drafts

    private func draftAction(_ draft: EmailDraft, _ action: DraftAction, instructions: String?) {
        guard draft.status == .pending, !model.draftBusy.contains(draft.draftID) else { return }
        let key = "\(draft.draftID)|\(draft.sha256)"
        model.draftNotice[draft.draftID] = nil
        model.draftLocalStatus[key] = action.optimisticStatus
        model.draftBusy.insert(draft.draftID)
        guard let api, !previewMode else {
            model.draftBusy.remove(draft.draftID)
            return
        }
        Task { [weak self] in
            do {
                try await api.draftAction(action, draft: draft, instructions: instructions)
                self?.model.draftBusy.remove(draft.draftID)
                await self?.refreshDraftSources()
            } catch DraftActionError.changed {
                guard let self else { return }
                self.model.draftLocalStatus[key] = nil
                self.model.draftBusy.remove(draft.draftID)
                self.model.draftNotice[draft.draftID] = draftChangedMessage
                await self.refreshDraftSources()
            } catch {
                guard let self else { return }
                self.model.draftLocalStatus[key] = nil
                self.model.draftBusy.remove(draft.draftID)
                self.model.draftNotice[draft.draftID] = "Couldn't \(action.rawValue): \(error.localizedDescription)"
            }
        }
    }

    /// Refetch tasks so the card shows the server's current draft (SSE also pushes it).
    private func refreshDraftSources() async {
        guard let api else { return }
        if let (work, tasks) = try? await api.latestWork() {
            if !tasks.isEmpty, tasks.map(\.id) == model.state.tasks.map(\.id) || model.state.workOnly || !model.state.connection.isOpen {
                dispatch(.tasks(tasks))
            } else if !tasks.isEmpty {
                var merged = model.state.tasks
                for task in tasks { if let i = merged.firstIndex(where: { $0.id == task.id }) { merged[i] = task } }
                dispatch(.tasks(merged))
            }
            if let work, work.runID == model.state.runID { dispatch(.work(work)) }
        }
    }

    public func hideIfIdle() {
        if !model.state.connection.isOpen && !model.state.workOnly && !previewMode { hideNow() }
    }

    private func handleEscape() {
        if model.selectedTaskID != nil && model.workExpanded { model.selectedTaskID = nil; surface.setNeedsResize() }
        else if model.workExpanded && !model.state.workOnly { model.workExpanded = false; surface.setNeedsResize() }
        else if model.state.workOnly { closeWorkView() }
        else if model.state.connection.isOpen { end() }
        else { hideNow() }
    }

    // MARK: Server events (SSE with polling fallback)

    private func startServerEvents(_ api: ServerClient, interactionID: String) {
        streamTask?.cancel()
        streamTask = Task { [weak self] in
            var parser = SSEParser()
            var attempt = 0
            while !Task.isCancelled {
                let outcome = await api.streamEvents(interactionID: interactionID, parser: &parser) { event in
                    await MainActor.run {
                        guard let self else { return }
                        for voiceEvent in parseServerStreamEvent(event) { self.dispatch(voiceEvent) }
                    }
                }
                guard let self, !Task.isCancelled else { return }
                switch outcome {
                case .unsupported:
                    self.startPollingFallback(api, interactionID: interactionID)
                    return
                case .ended:
                    attempt = 0
                    if !self.model.state.connection.isInCall { return }
                case .failed:
                    attempt += 1
                    if attempt >= 4 { self.startPollingFallback(api, interactionID: interactionID); return }
                }
                let delay = sseReconnectDelay(attempt: attempt, serverRetryMilliseconds: parser.retryMilliseconds)
                try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            }
        }
    }

    /// Server without `/events`: poll instead.
    private func startPollingFallback(_ api: ServerClient, interactionID: String) {
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            var lastWork = Date.distantPast
            while !Task.isCancelled {
                guard let self, self.model.state.connection.isInCall else { return }
                if let snapshot = try? await api.interaction(interactionID) { self.dispatch(.interaction(snapshot)) }
                if let run = self.model.state.runID, Date().timeIntervalSince(lastWork) > 2 {
                    lastWork = Date()
                    if let work = try? await api.work(runID: run) { self.dispatch(.work(work)) }
                }
                try? await Task.sleep(nanoseconds: 700_000_000)
            }
        }
    }

    // MARK: Work view, approval, stop

    public func showWork(active: Bool) {
        if model.state.connection.isOpen {
            model.workExpanded = true
            surface.setNeedsResize()
            surface.focus()
            return
        }
        guard let api else { return }
        dispatch(.workOnlyOpened)
        model.workExpanded = true
        surface.onEscape = { [weak self] in self?.handleEscape() }
        surface.focus()
        startTicker()
        workOnlyTask?.cancel()
        workOnlyTask = Task { [weak self] in
            while !Task.isCancelled {
                do {
                    let (work, tasks) = try await api.latestWork()
                    guard let self, self.model.state.workOnly else { return }
                    self.dispatch(.work(work))
                    if tasks != self.model.state.tasks { self.dispatch(.tasks(tasks)) }
                } catch {
                    self?.dispatch(.error("Work unavailable: \(error.localizedDescription)"))
                }
                try? await Task.sleep(nanoseconds: 2_000_000_000)
            }
        }
    }

    /// "Show me": open the task's live image (detail view) or bring its review card forward.
    private func act(on show: ShowRequest) {
        if model.showsSlim { model.slim = false }
        if show.opensDetail {
            if model.state.tasks.contains(where: { $0.id == show.taskID }) { selectTask(show.taskID) }
        } else {
            model.workExpanded = false
            model.selectedTaskID = nil
            if let run = show.runID { model.focusedReviewID = run }
            model.objectWillChange.send()
            surface.setNeedsResize()
        }
        if !surface.isVisible { showPanel() }
    }

    private func selectTask(_ id: String?) {
        model.selectedTaskID = id
        model.workExpanded = true
        surface.setNeedsResize()
        surface.focus()
    }

    private func toggleWorkView() {
        if model.showsSlim { model.slim = false; model.workExpanded = true }
        else { model.workExpanded.toggle() }
        surface.setNeedsResize()
        if model.workExpanded { surface.focus() }
    }

    private func closeWorkView() {
        if model.selectedTaskID != nil && model.state.tasks.count > 1 {
            model.selectedTaskID = nil   // Back goes from one task to the task list
            surface.setNeedsResize()
            return
        }
        model.selectedTaskID = nil
        model.workExpanded = false
        if model.state.workOnly {
            workOnlyTask?.cancel(); workOnlyTask = nil
            dispatch(.reset)
            surface.hide()
            stopTicker()
        } else {
            surface.setNeedsResize()
        }
    }

    private func resolveApproval(_ choice: String) {
        guard choice == "once" || choice == "deny", let api,
              let approval = model.state.approval, let id = model.state.interactionID, !model.busy else { return }
        model.busy = true
        Task { [weak self] in
            do {
                try await api.resolveApproval(interactionID: id, approval: approval, choice: choice)
                self?.dispatch(.approvalResolved)
            } catch {
                self?.dispatch(.error(error.localizedDescription))
            }
            self?.model.busy = false
        }
    }

    private func stopWork() {
        guard let run = model.state.runID else { return }
        stopWork(runID: run)
    }

    /// Stop one task. While paused the adopted runs still belong to the paused
    /// call's interaction, so Stop goes there.
    /// The x on finished tasks: hide now, then tell the api so they stay gone.
    private func dismissTasks(_ runIDs: [String]) {
        guard !runIDs.isEmpty else { return }
        if let id = model.selectedTaskID, let task = model.state.tasks.first(where: { $0.id == id }),
           runIDs.contains(task.info.runID ?? "") {
            model.selectedTaskID = nil
        }
        dispatch(.tasksDismissed(runIDs))
        surface.setNeedsResize()
        guard let api, !previewMode else { return }
        Task { [weak self] in
            do { try await api.dismissTasks(runIDs: runIDs) }
            catch { self?.dispatch(.error("Couldn't clear tasks: \(error.localizedDescription)")) }
        }
    }

    /// Dismiss on a review card: hide now, then tell the api so it stays gone on the next call.
    private func dismissReview(_ runID: String) {
        guard let review = model.state.pendingReviews.first(where: { $0.runID == runID }) else { return }
        let cards = review.images.map(\.number)
        dispatch(.reviewDismissed(runID, cards))
        model.reviewIndex[runID] = nil
        surface.setNeedsResize()
        guard let api, !previewMode else { return }
        Task { [weak self] in
            do { try await api.dismissReview(runID: runID, cards: cards) }
            catch { self?.dispatch(.error("Couldn't dismiss: \(error.localizedDescription)")) }
        }
    }

    private func stopWork(runID run: String) {
        guard let api, let id = model.state.interactionID ?? model.state.resumeFrom else { return }
        Task { [weak self] in
            do { try await api.cancelBackend(interactionID: id, runID: run) }
            catch { self?.dispatch(.error(error.localizedDescription)) }
        }
    }

    // MARK: Preview

    public func showPreview(_ state: VoiceState, workExpanded: Bool, captionExpanded: Bool = false) {
        previewMode = true
        model.state = state
        model.workExpanded = workExpanded
        model.captionExpanded = captionExpanded
        let p = present(state)
        model.shownStatus = p.secondary
        model.shownTone = p.tone
        #if os(macOS)
        surface.onEscape = { NSApp.terminate(nil) }
        #endif
        surface.show()
    }
}
