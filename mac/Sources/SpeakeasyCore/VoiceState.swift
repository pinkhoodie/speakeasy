import Foundation

// Four independent state axes. Each event has one owner axis; in particular
// work/api events can never change `speech`, and speech events never change `work`.

public enum Finalization: Equatable, Sendable { case complete, incomplete }

public enum ConnectionPhase: Equatable, Sendable {
    case idle
    case connecting
    case live
    case ending
    /// Pause: the billed voice session is closed, the conversation and its
    /// tasks are kept, and Resume opens a new session that picks up from here.
    case paused
    case ended(Finalization)
    case failed(String)

    public var isInCall: Bool {
        switch self { case .connecting, .live, .ending: return true; default: return false }
    }
    /// In a call or paused: the conversation is still open.
    public var isOpen: Bool { isInCall || self == .paused }
}

public enum MicState: Equatable, Sendable { case live, muted }

public enum SpeechState: Equatable, Sendable {
    case idle
    /// the assistant is speaking; `lastDelta` is when the latest output transcript delta arrived.
    case speaking(lastDelta: Date)
    /// Remote audio disabled by Quiet reply until the user speaks again.
    case quieted
}

public enum WorkPhase: Equatable, Sendable {
    case none
    case waiting(since: Date)
    case active(short: String, detail: String?, source: String?, updatedAt: Date)
    case stale
    case approval(ApprovalInfo)
    case done(status: String, result: WorkResult?)
}

public struct Exchange: Equatable, Sendable {
    public var you = ""
    public var assistant = ""
    public var replyStarted = false
    /// Input received after a reply that so far contains only non-speech tags
    /// (e.g. `[cough]`); it must not clear the visible exchange.
    public var pendingInput = ""
    public init(you: String = "", assistant: String = "", replyStarted: Bool = false) {
        self.you = you; self.assistant = assistant; self.replyStarted = replyStarted
    }
    /// Raw provider text. Views render `cleaned`, which hides bracketed tags.
    public var isEmpty: Bool { cleaned.you.isEmpty && cleaned.assistant.isEmpty }
    public var cleaned: Exchange {
        Exchange(you: cleanTranscript(you), assistant: cleanTranscript(assistant), replyStarted: replyStarted)
    }
}

/// One side's turn in the running conversation. Nothing here is ever erased
/// during a call: a new turn only appends.
public struct TranscriptTurn: Equatable, Sendable, Identifiable {
    public enum Speaker: Equatable, Sendable { case you, assistant }
    public var id: Int
    public var speaker: Speaker
    public var text: String
    public init(id: Int, speaker: Speaker, text: String) { self.id = id; self.speaker = speaker; self.text = text }
    public var cleanedText: String { cleanTranscript(text) }
}

public struct VoiceState: Equatable, Sendable {
    public var connection: ConnectionPhase = .idle
    public var mic: MicState = .live
    public var speech: SpeechState = .idle
    public var work: WorkPhase = .none
    public var exchange = Exchange()
    public var interactionID: String?
    public var runID: String?
    public var workInfo: WorkInfo?
    public var approval: ApprovalInfo?
    public var delegationAt: Date?
    public var supersededRunIDs: Set<String> = []
    public var lastError: String?
    /// Opened from the menu bar without a call (never billed).
    public var workOnly = false
    /// Every task of this call (parallel runs), oldest first.
    public var tasks: [TaskItem] = []
    /// Finished tasks the user cleared; kept out of later task snapshots.
    public var dismissedRunIDs: Set<String> = []
    /// Closing the session to pause (not end) the call.
    public var pausing = false
    /// The paused call the current connection is resuming.
    public var resumeFrom: String?
    /// The whole call so far, oldest first (kept across Pause/Resume).
    public var transcript: [TranscriptTurn] = []
    /// When the last piece of the user's speech arrived; transcription of his words
    /// can trail behind the assistant's first words by a moment.
    public var lastInputAt: Date?
    /// Last sign of conversation: either side's speech, a mic change, or an approval answer.
    public var lastActivityAt: Date?
    public var now: Date = Date(timeIntervalSince1970: 0)
    /// Display name of the assistant (from the server's `assistant_name`); never a persona baked in.
    public var assistantName: String = VoiceState.defaultAssistantName
    /// Quiet time before a live call auto-pauses (server `idle_pause_minutes`; 0 disables).
    public var idleTimeout: TimeInterval = VoiceState.defaultIdleTimeout
    /// Hint shown on the idle panel (the user's call shortcut, e.g. "⌃⌥Space").
    public var callShortcutHint: String = "Control–Option–Space"

    public init() {}

    public static let defaultAssistantName = "Hermes"

    /// A fresh state that keeps the per-install configuration (name, idle timeout).
    public func fresh() -> VoiceState {
        var s = VoiceState()
        s.assistantName = assistantName
        s.idleTimeout = idleTimeout
        s.callShortcutHint = callShortcutHint
        return s
    }

    /// A live call with no speech from either side for this long closes its paid voice
    /// session (pause, so Resume keeps the conversation and tasks keep running).
    public static let defaultIdleTimeout: TimeInterval = 5 * 60
    public static let speechQuietGap: TimeInterval = 6
    /// the user's words arriving this soon after his previous words are the tail
    /// of the same sentence, even if the assistant has already started replying.
    public static let lateInputGrace: TimeInterval = 2.5
    public static let staleAfter: TimeInterval = 90
    public static let freshFor: TimeInterval = 45

    /// Remote audio should play unless the user quieted the assistant.
    public var remoteAudioEnabled: Bool { speech != .quieted }
    /// Local capture track state; muted keeps the call (and billing) open.
    public var localAudioEnabled: Bool { mic == .live }
    public var activeTasks: [TaskItem] { tasks.filter(\.isActive) }
    public var canPause: Bool { connection == .live && interactionID != nil && !pausing }
    /// Five quiet minutes on a live call. Not while the assistant is talking or an approval waits on the user.
    public func idleExpired(at now: Date) -> Bool {
        guard connection == .live, !pausing, approval == nil, let last = lastActivityAt else { return false }
        if case .speaking = speech { return false }
        guard idleTimeout > 0 else { return false }
        return now.timeIntervalSince(last) >= idleTimeout
    }
}

public enum VoiceEvent: Equatable, Sendable {
    case startRequested
    case workOnlyOpened
    case sessionAdmitted(interactionID: String)
    case sessionStarted
    case inputDelta(String)
    case outputDelta(String)
    case delegationCreated
    case sessionClosed
    case serverClosed(Finalization, error: String?)
    case endRequested
    case endTimedOut
    case transportLost
    case failed(String)
    case error(String)
    case toggleMic
    case setMic(MicState)
    case quietAssistant
    case work(WorkInfo?)
    case interaction(InteractionSnapshot)
    case approval(ApprovalInfo?)
    case approvalResolved
    case tasks([TaskItem])
    /// the user cleared finished tasks (the x); hidden right away, api confirms.
    case tasksDismissed([String])
    case pauseRequested
    /// The api refused to hold the call: stay live.
    case pauseFailed(String)
    case resumeRequested
    case tick
    case reset
}

/// Pure reducer. `now` is supplied by the caller so tests are deterministic.
public func reduce(_ state: VoiceState, _ event: VoiceEvent, now: Date) -> VoiceState {
    var s = state
    s.now = now
    switch event {
    case .startRequested:
        s = s.fresh()
        s.now = now
        s.connection = .connecting

    case .workOnlyOpened:
        if !s.connection.isInCall {
            s = s.fresh()
            s.now = now
            s.workOnly = true
        }

    case .sessionAdmitted(let id):
        s.interactionID = id

    case .pauseRequested:
        if s.canPause { s.pausing = true; s.connection = .ending; s.speech = .idle }

    case .pauseFailed(let message):
        if s.pausing && s.connection == .ending { s.connection = .live }
        s.pausing = false
        s.lastError = message

    case .resumeRequested:
        guard s.connection == .paused else { break }
        // Keep the conversation, tasks and mic choice; open a new session.
        s.resumeFrom = s.interactionID
        s.interactionID = nil
        s.connection = .connecting
        s.speech = .idle
        s.lastError = nil
        s.exchange.replyStarted = true   // next words start a fresh visible exchange
        s.lastInputAt = nil

    case .tasks(let tasks):
        s.tasks = tasks.filter { !s.dismissedRunIDs.contains($0.info.runID ?? "") }

    case .tasksDismissed(let runIDs):
        let ids = Set(runIDs)
        let cleared = s.tasks.filter { $0.isSettled && ids.contains($0.info.runID ?? "") }
        s.dismissedRunIDs.formUnion(cleared.compactMap(\.info.runID))
        s.tasks.removeAll { task in cleared.contains(task) }

    case .sessionStarted:
        if s.connection == .connecting { s.connection = .live }
        s.lastActivityAt = now

    case .inputDelta(let text):
        guard !text.isEmpty else { break }
        let heardBefore = cleanTranscript(s.exchange.you)
        let heardWords: Bool
        // The end of the user's sentence can be transcribed after the assistant'starts
        // talking; that is the same turn, not a new one.
        let unfinished = heardBefore.last.map { !".?!".contains($0) } ?? false
        let trailing = s.exchange.replyStarted && unfinished && s.exchange.pendingInput.isEmpty
            && s.lastInputAt.map { now.timeIntervalSince($0) < VoiceState.lateInputGrace } == true
        if s.exchange.replyStarted && !trailing {
            let candidate = s.exchange.pendingInput + text
            // A cough or throat-clear after the assistant's reply is not a new turn.
            if cleanTranscript(candidate).isEmpty { s.exchange.pendingInput = candidate; break }
            s.exchange = Exchange(you: candidate)
            s.transcript.append(TranscriptTurn(id: s.transcript.count, speaker: .you, text: candidate))
            heardWords = true
        } else {
            s.exchange.you += text
            if trailing, let i = s.transcript.lastIndex(where: { $0.speaker == .you }) {
                s.transcript[i].text += text
            } else if let last = s.transcript.last, last.speaker == .you {
                s.transcript[s.transcript.count - 1].text += text
            } else {
                s.transcript.append(TranscriptTurn(id: s.transcript.count, speaker: .you, text: text))
            }
            heardWords = cleanTranscript(s.exchange.you) != heardBefore
        }
        s.lastInputAt = now
        if heardWords { s.lastActivityAt = now }
        // the user speaking real words again un-quiets the assistant and interrupts the speaking label.
        if s.mic == .live && heardWords { s.speech = .idle }

    case .outputDelta(let text):
        guard !text.isEmpty else { break }
        s.exchange.replyStarted = true
        s.exchange.assistant += text
        s.lastActivityAt = now
        if let last = s.transcript.last, last.speaker == .assistant {
            s.transcript[s.transcript.count - 1].text += text
        } else {
            s.transcript.append(TranscriptTurn(id: s.transcript.count, speaker: .assistant, text: text))
        }
        if s.speech != .quieted { s.speech = .speaking(lastDelta: now) }

    case .delegationCreated:
        if let old = s.runID { s.supersededRunIDs.insert(old) }
        s.runID = nil
        s.workInfo = nil
        s.delegationAt = now

    case .sessionClosed:
        if !isEnded(s.connection) && s.connection != .paused { s.connection = .ended(.complete) }
        s.speech = .idle

    case .serverClosed(let fin, let error):
        if !isEnded(s.connection) && s.connection != .paused { s.connection = .ended(fin) }
        if let error, !s.pausing { s.lastError = error }
        s.speech = .idle

    case .endRequested:
        switch s.connection {
        case .connecting where s.interactionID == nil:
            s.connection = .ended(.complete)   // nothing admitted, nothing billed
        case .connecting, .live:
            s.connection = .ending
        case .ending where s.pausing:
            s.pausing = false   // End while pausing: the close already in flight ends the call
        case .paused:
            s.connection = .ended(.complete)   // voice session already closed; nothing billed
        default: break
        }

    case .endTimedOut:
        if s.connection == .ending { s.connection = .ended(.incomplete) }

    case .transportLost:
        switch s.connection {
        case .live, .connecting:
            s.connection = .ended(.incomplete)
            s.lastError = "Voice connection lost; final usage confirmation unavailable"
            s.speech = .idle
        default: break   // .ending: wait for api finalization or the deadline
        }

    case .failed(let message):
        if !isEnded(s.connection) { s.connection = .failed(message) }
        s.lastError = message
        s.speech = .idle

    case .error(let message):
        s.lastError = message

    case .toggleMic:
        if s.connection == .live || s.connection == .connecting { s.mic = s.mic == .live ? .muted : .live; s.lastActivityAt = now }

    case .setMic(let mic):
        if s.connection == .live || s.connection == .connecting { s.mic = mic; s.lastActivityAt = now }

    case .quietAssistant:
        if s.connection == .live { s.speech = .quieted }

    case .work(let info):
        if let info {
            if let run = info.runID, s.supersededRunIDs.contains(run) { break }
            if let current = s.runID, let run = info.runID, run != current { break }
            if s.runID == nil { s.runID = info.runID }
        }
        s.workInfo = info

    case .interaction(let snapshot):
        if let run = snapshot.runID, !s.supersededRunIDs.contains(run), run != s.runID {
            if let old = s.runID { s.supersededRunIDs.insert(old) }
            s.runID = run
            if s.workInfo?.runID != run { s.workInfo = nil }
            if s.delegationAt == nil { s.delegationAt = now }
        }
        s.approval = snapshot.approval
        if let error = snapshot.error { s.lastError = error }
        switch snapshot.finalization {
        case "confirmed", "complete":
            if s.connection.isInCall && s.connection != .connecting { s.connection = .ended(.complete) }
        case "incomplete":
            if s.connection == .ending { s.connection = .ended(.incomplete) }
        default: break
        }

    case .approval(let approval):
        s.approval = approval

    case .approvalResolved:
        s.approval = nil
        s.lastActivityAt = now

    case .tick:
        if case .speaking(let last) = s.speech, now.timeIntervalSince(last) >= VoiceState.speechQuietGap {
            s.speech = .idle
        }

    case .reset:
        s = s.fresh()
        s.now = now
    }
    // A pausing call that closes (cleanly or not) is paused, not ended: the
    // api holds its transcript and tasks for Resume.
    if s.pausing, case .ended = s.connection {
        s.connection = .paused
        s.pausing = false
        s.speech = .idle
        if s.lastError == "Voice connection lost; final usage confirmation unavailable" { s.lastError = nil }
    }
    s.work = deriveWork(s, now: now)
    return s
}

private func isEnded(_ phase: ConnectionPhase) -> Bool {
    if case .ended = phase { return true }
    if case .failed = phase { return true }
    return false
}

/// Work axis is a pure function of api-owned inputs; never of speech or mic.
public func deriveWork(_ s: VoiceState, now: Date) -> WorkPhase {
    if let approval = s.approval { return .approval(approval) }
    guard let info = s.workInfo else {
        if let since = s.delegationAt { return .waiting(since: since) }
        return .none
    }
    if info.isTerminal { return .done(status: info.status, result: info.result) }
    if info.stale { return .stale }
    if info.status == "waiting_for_approval" { return waitingFallback(info, s, now) }
    // Local staleness: the api row itself has not moved for 90 s.
    if let heartbeat = info.updated ?? info.updatedAt, now.timeIntervalSince(heartbeat) > VoiceState.staleAfter {
        return .stale
    }
    if let short = info.shortStatus, short != "Status unconfirmed", let at = info.updatedAt ?? info.updated {
        return .active(short: short, detail: info.detail, source: info.statusSource, updatedAt: at)
    }
    return waitingFallback(info, s, now)
}

private func waitingFallback(_ info: WorkInfo, _ s: VoiceState, _ now: Date) -> WorkPhase {
    // Approval pending per work status, but the approval object hasn't arrived: honest waiting.
    let since = info.events.first { $0.kind == "request" }?.at ?? s.delegationAt ?? info.updated ?? now
    return .waiting(since: since)
}
