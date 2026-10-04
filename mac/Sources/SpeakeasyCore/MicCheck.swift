import Foundation

/// Whether the call's microphone is really reaching the voice.
/// "Listening" is only shown once it is; a dead mic is repaired automatically.
public enum MicHealth: Equatable, Sendable {
    /// Call just went live; waiting for proof the mic works.
    case checking
    /// Sound is arriving from the mic and being sent.
    case ok
    /// The mic wasn't getting through; the voice connection is being reopened.
    case repairing
    /// Still not working after reopening; the user has to act.
    case broken
}

/// Why the mic was judged dead (for logs and tests).
public enum MicFault: String, Equatable, Sendable {
    case noSound = "no sound from the mic"
    case notSending = "mic audio not being sent"
    case unanswered = "spoke but the voice never heard it"
}

/// Checks, from the device's own call stats, that the mic is really getting through:
///  1. the mic delivers samples (a working mic never reads a flat zero, even in a silent room);
///  2. those samples are leaving the device (audio packets sent keeps growing);
///  3. when you clearly speak, the voice hears you (a transcript or a reply follows).
/// Pure and clock-driven so every rule is testable without audio.
public struct MicCheck: Sendable {
    /// Levels at or below this count as "no samples at all" (not "quiet room").
    public static let deadLevel = 0.000_01
    /// Clear speech (WebRTC's linear 0...1 audio level).
    public static let speechLevel = 0.05
    /// A newly live, unmuted call must show sound and outgoing audio within this long.
    public static let firstSoundWithin: TimeInterval = 2.5
    /// Later: this long of flat zero, or of no audio sent, means the mic died mid-call.
    public static let deadAfter: TimeInterval = 4
    /// Speech at least this long counts as talking to the assistant…
    public static let utteranceMin: TimeInterval = 1.2
    /// …and ends after this much quiet…
    public static let utteranceGap: TimeInterval = 0.7
    /// …and the voice must show it heard within this long after it ends.
    public static let answerWithin: TimeInterval = 7
    /// Automatic reopen attempts per call before asking the user to restart.
    public static let maxRepairs = 2

    public private(set) var health: MicHealth = .checking
    public private(set) var repairs = 0
    public private(set) var lastFault: MicFault?
    private var watchingSince: Date?
    private var lastSound: Date?
    private var lastPackets: Int?
    private var lastPacketsGrew: Date?
    private var speechStart: Date?
    private var lastSpeech: Date?
    private var unansweredSince: Date?
    /// The voice has heard the user on this connection. Rule 3 only guards the start of a
    /// connection (the failure seen: calls that never register a word), so a noisy room
    /// mid-conversation can't trigger needless reconnects.
    private var heardAny = false

    public init() {}

    /// A voice connection is live (call start, or after a repair): judge it afresh.
    public mutating func connectionOpened() {
        reset()
        if health != .broken { health = .checking }
    }

    /// A repair (reopen of the voice connection) is starting.
    public mutating func repairStarted() {
        repairs += 1
        health = .repairing
        reset()
    }

    /// The voice showed it heard the user (a transcript of their words, or it replied).
    public mutating func heard() {
        heardAny = true
        unansweredSince = nil
        speechStart = nil
    }

    /// Feed one stats sample. `listening` = call live, mic unmuted, the call owns the mic and the
    /// assistant isn't talking. `packetsSent` = outgoing audio packets so far (nil when unknown).
    /// Returns a fault when the mic is judged dead and a repair should start now.
    public mutating func sample(level: Double?, packetsSent: Int?, listening: Bool, now: Date) -> MicFault? {
        guard listening, health != .repairing, health != .broken else {
            // Nothing to judge; restart the clocks when listening resumes.
            watchingSince = nil; speechStart = nil
            return nil
        }
        if watchingSince == nil { watchingSince = now }
        let since = watchingSince!
        if let packetsSent {
            if lastPackets == nil || packetsSent > lastPackets! { lastPacketsGrew = now }
            lastPackets = packetsSent
        }
        // No level reported at all (stats shape unknown): don't guess, assume fine.
        guard let level else { if health == .checking { health = .ok }; return nil }
        if level > Self.deadLevel { lastSound = now }

        let soundOK = lastSound.map { now.timeIntervalSince($0) < Self.deadAfter } ?? false
        let sendingOK = packetsSent == nil || (lastPacketsGrew.map { now.timeIntervalSince($0) < Self.deadAfter } ?? false)
        if soundOK && sendingOK && health == .checking { health = .ok }

        let grace = lastSound == nil ? Self.firstSoundWithin : Self.deadAfter
        if now.timeIntervalSince(since) >= grace {
            if !soundOK { return fault(.noSound) }
            if !sendingOK { return fault(.notSending) }
        }

        // Clear speech that the voice never acknowledged.
        if heardAny { return nil }
        if level >= Self.speechLevel {
            if speechStart == nil { speechStart = now }
            lastSpeech = now
        } else if let start = speechStart, let last = lastSpeech, now.timeIntervalSince(last) >= Self.utteranceGap {
            if last.timeIntervalSince(start) >= Self.utteranceMin, unansweredSince == nil { unansweredSince = last }
            speechStart = nil
        }
        if let pending = unansweredSince, now.timeIntervalSince(pending) >= Self.answerWithin {
            return fault(.unanswered)
        }
        return nil
    }

    private mutating func fault(_ kind: MicFault) -> MicFault? {
        lastFault = kind
        if repairs >= Self.maxRepairs {
            health = .broken
            return nil
        }
        return kind
    }

    private mutating func reset() {
        watchingSince = nil; lastSound = nil; lastPackets = nil; lastPacketsGrew = nil
        speechStart = nil; lastSpeech = nil; unansweredSince = nil; heardAny = false
    }
}
