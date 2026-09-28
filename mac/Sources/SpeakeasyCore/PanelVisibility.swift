import Foundation

/// What the call hotkey (and menu toggle) does in each situation. Pure so the
/// "panel stuck after a call" bug stays fixed under test.
public enum HotkeyAction: Equatable, Sendable {
    /// Idle and hidden (or never shown): start a call.
    case startCall
    /// A call is connecting, live, ending or paused: end it.
    case endCall
    /// Idle but the panel is up (after a call, or showing work): hide it. Never a no-op.
    case hidePanel
    /// A call is open but its panel was closed with the x: bring it back (never ends the call blind).
    case showPanel
}

public func hotkeyAction(connection: ConnectionPhase, panelVisible: Bool) -> HotkeyAction {
    if connection.isOpen {
        if !panelVisible { return .showPanel }
        return connection == .ending ? .hidePanel : .endCall
    }
    return panelVisible ? .hidePanel : .startCall
}

/// Something on the panel still needs the user: an approval, a task waiting on one, an email
/// draft, or finished images waiting on a review card.
public func panelNeedsAttention(_ state: VoiceState) -> Bool {
    state.approval != nil || state.tasks.contains { $0.info.status == "waiting_for_approval" }
        || !state.pendingDrafts.isEmpty || !state.pendingReviews.isEmpty
}

/// After a call ends the panel shows the finished state briefly, then hides itself.
/// Hovering or anything needing attention holds it open; the countdown restarts
/// when the pointer leaves.
public struct PanelAutoHide: Equatable, Sendable {
    public static let defaultDelay: TimeInterval = 4
    public let delay: TimeInterval
    public private(set) var deadline: Date?

    public init(delay: TimeInterval = PanelAutoHide.defaultDelay) { self.delay = delay }

    public var isArmed: Bool { deadline != nil }

    /// A call just ended (or the idle panel was shown): start the countdown.
    public mutating func arm(now: Date) { deadline = now + delay }

    /// A call started, the user opened work, or the panel was hidden: stop counting.
    public mutating func cancel() { deadline = nil }

    /// Call on every tick; true means "hide the panel now" (and disarms).
    public mutating func shouldHide(now: Date, hovering: Bool, needsAttention: Bool) -> Bool {
        guard let deadline else { return false }
        if hovering || needsAttention {
            self.deadline = now + delay   // hold, then a fresh grace period once released
            return false
        }
        guard now >= deadline else { return false }
        self.deadline = nil
        return true
    }
}

/// Slim mode's one-line task summary: "2 running · 1 done", or "N task(s) need you"
/// when an approval or a pending email draft is waiting. nil when there are no tasks.
public func slimTaskSummary(_ tasks: [TaskItem], approvalPending: Bool = false) -> String? {
    guard !tasks.isEmpty || approvalPending else { return nil }
    var waiting = tasks.filter { $0.info.status == "waiting_for_approval" || !$0.info.pendingDrafts.isEmpty }.count
    if approvalPending && waiting == 0 { waiting = 1 }
    if waiting > 0 { return waiting == 1 ? "1 task needs you" : "\(waiting) tasks need you" }
    let active = tasks.filter(\.isActive).count
    let total = tasks.count
    if active == 0 { return total == 1 ? "1 task done" : "\(total) tasks done" }
    return active == total ? (active == 1 ? "1 task running" : "\(active) tasks running")
        : "\(active) running · \(total - active) done"
}
