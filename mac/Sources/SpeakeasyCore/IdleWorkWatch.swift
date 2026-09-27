import Foundation

/// What the app should tell the user when watched work settles while no call is open.
public struct WorkNotice: Equatable, Sendable {
    public enum Kind: Equatable, Sendable { case completed, failed, approval }
    public var kind: Kind
    public var runID: String?
    public var title: String
    public var body: String
}

/// Pure scheduling + transition logic for idle (no-call) continuity.
///
/// Poll `GET /voice/work/latest` every `interval` seconds only while the last known
/// run is non-terminal; stop at terminal; back off exponentially on errors. After a
/// terminal state nothing polls until `callEnded` seeds a new watch.
public struct IdleWorkWatch: Equatable, Sendable {
    public static let interval: TimeInterval = 20
    public static let maxBackoff: TimeInterval = 300
    /// Server states after which the run will not change on its own.
    public static let finalStatuses: Set<String> = WorkInfo.terminalStatuses.union(["ambiguous"])

    public private(set) var runID: String?
    public private(set) var status: String?
    public private(set) var errors = 0
    public private(set) var active = false

    public init() {}

    /// A call just ended. Watch its last run only if that run is still in flight.
    public mutating func callEnded(lastWork: WorkInfo?) {
        runID = lastWork?.runID
        status = lastWork?.status
        errors = 0
        active = lastWork.map { !Self.finalStatuses.contains($0.status) } ?? false
    }

    /// A new call started: the in-call stream owns updates now.
    public mutating func stop() { active = false; errors = 0 }

    /// Seconds until the next poll, or nil when nothing should poll.
    public var nextDelay: TimeInterval? {
        guard active else { return nil }
        guard errors > 0 else { return Self.interval }
        return min(Self.interval * pow(2, Double(errors)), Self.maxBackoff)
    }

    public mutating func observeError() {
        guard active else { return }
        errors += 1
    }

    /// Feed one poll result; returns a notice on a meaningful transition.
    public mutating func observe(_ work: WorkInfo?, assistantName name: String = VoiceState.defaultAssistantName) -> WorkNotice? {
        guard active else { return nil }
        errors = 0
        guard let work else { return nil }
        if let runID, work.runID != runID {
            // Latest is a different run (e.g. started elsewhere); only follow ours.
            return nil
        }
        let previous = status
        status = work.status
        if Self.finalStatuses.contains(work.status) { active = false }
        guard previous != work.status else { return nil }
        switch work.status {
        case "completed":
            let body = work.result?.spoken ?? "\(name) finished. Details are in Recent work."
            return WorkNotice(kind: .completed, runID: work.runID, title: name, body: body)
        case "failed", "interrupted":
            return WorkNotice(kind: .failed, runID: work.runID, title: name,
                              body: work.result?.spoken ?? "\(name)'s work stopped before finishing.")
        case "waiting_for_approval":
            return WorkNotice(kind: .approval, runID: work.runID, title: name, body: "Needs your approval")
        default:
            return nil
        }
    }
}


/// Heads-up while a call is paused: the paused-call poll compares the task list
/// before and after, and each task that just settled (finished, failed, or needs
/// approval) becomes one notice. Pure so the transition rules are testable.
public enum PausedTaskNotices {
    public static func settled(before: [TaskItem], after: [TaskItem],
                               assistantName name: String = VoiceState.defaultAssistantName) -> [WorkNotice] {
        let previous = Dictionary(before.map { ($0.id, $0.info.status) }, uniquingKeysWith: { _, new in new })
        var notices: [WorkNotice] = []
        for task in after {
            guard let was = previous[task.id], was != task.info.status else { continue }
            let what = (task.info.title ?? task.info.askedFor).map { "“\(shorten($0))”" }
            switch task.info.status {
            case "completed":
                let body = task.info.result?.spoken ?? what.map { "Finished \($0)." } ?? "A task finished."
                notices.append(WorkNotice(kind: .completed, runID: task.info.runID, title: "\(name) · call paused",
                                          body: body))
            case "failed", "interrupted":
                notices.append(WorkNotice(kind: .failed, runID: task.info.runID, title: "\(name) · call paused",
                                          body: what.map { "Couldn't finish \($0)." } ?? "A task stopped before finishing."))
            case "waiting_for_approval":
                notices.append(WorkNotice(kind: .approval, runID: task.info.runID, title: "\(name) · call paused",
                                          body: what.map { "Needs your approval: \($0)" } ?? "Needs your approval"))
            default:
                continue
            }
        }
        return notices
    }

    static func shorten(_ text: String, limit: Int = 80) -> String {
        let flat = text.split(whereSeparator: \.isNewline).joined(separator: " ")
        return flat.count <= limit ? flat : String(flat.prefix(limit - 1)) + "…"
    }
}
