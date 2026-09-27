import SwiftUI
import SpeakeasyCore

/// Observable model the SwiftUI panel renders. The controller owns all mutation.
@MainActor
final class VoicePanelModel: ObservableObject {
    static let defaultWidth: CGFloat = 400
    @Published var state = VoiceState()
    @Published var workExpanded = false
    @Published var captionExpanded = false
    /// Status line text after the minimum-dwell debounce.
    @Published var shownStatus = ""
    @Published var shownTone: StatusTone = .plain
    @Published var busy = false
    /// Live captions of the conversation (Settings › General).
    @Published var showCaptions = true
    /// e.g. "⌃⌥M: tap to mute/unmute, hold to talk". Empty when no shortcut.
    @Published var muteShortcutHint = ""
    /// the user's panel size: width, plus extra height given to the transcript and
    /// Work scroll areas. Set by dragging the panel's right/bottom edge.
    @Published var panelWidth: CGFloat = VoicePanelModel.defaultWidth
    @Published var extraHeight: CGFloat = 0

    /// Slim mode: just the header row (status, pause, mic, end) plus a one-line task
    /// summary. Approvals and pending email drafts still show. Remembered across calls.
    @Published var slim: Bool = UserDefaults.standard.bool(forKey: VoicePanelModel.slimKey) {
        didSet { UserDefaults.standard.set(slim, forKey: VoicePanelModel.slimKey) }
    }
    nonisolated static let slimKey = "panel.slim"
    var onToggleSlim: () -> Void = {}
    /// Slim only applies to an open call; a finished or work-only panel is always full.
    var showsSlim: Bool { slim && !state.workOnly && state.connection.isOpen }

    var onToggleMic: () -> Void = {}
    var onStart: () -> Void = {}
    var onEnd: () -> Void = {}
    var onApproval: (String) -> Void = { _ in }
    var onStopWork: () -> Void = {}
    var onCloseWork: () -> Void = {}
    var onToggleWork: () -> Void = {}
    var onTogglePause: () -> Void = {}
    var onStopTask: (String) -> Void = { _ in }
    /// nil shows the task list; an id opens that task.
    var onSelectTask: (String?) -> Void = { _ in }
    /// Clear finished tasks (run ids) from the list.
    var onDismissTasks: ([String]) -> Void = { _ in }
    /// Exact-run, authenticated image load through the api; no retailer request from the app.
    var loadProductImage: (String, Int) async -> Data? = { _, _ in nil }
    /// Task opened from the list (Work view), nil = list / current task.
    @Published var selectedTaskID: String?
    /// e.g. "⌃⌥P". Empty when no shortcut.
    @Published var pauseShortcutHint = ""

    // MARK: Email drafts
    /// Approve/Deny/Revise on a draft (draft as shown, action, revise instructions).
    var onDraftAction: (EmailDraft, DraftAction, String?) -> Void = { _, _, _ in }
    /// Drafts with a request in flight (buttons disabled).
    @Published var draftBusy: Set<String> = []
    /// Optimistic status after a tap, keyed "draftID|sha256" so a new version starts clean.
    @Published var draftLocalStatus: [String: EmailDraft.Status] = [:]
    /// Per-draft notice, e.g. after a 409 "The draft changed — review it again".
    @Published var draftNotice: [String: String] = [:]

    func displayedStatus(of draft: EmailDraft) -> EmailDraft.Status {
        guard draft.status == .pending else { return draft.status }   // the server has moved on
        return draftLocalStatus["\(draft.draftID)|\(draft.sha256)"] ?? .pending
    }

    /// Pending drafts to pin in the call panel (like approvals).
    var pinnedDrafts: [EmailDraft] {
        state.pendingDrafts.filter { displayedStatus(of: $0) == .pending || draftBusy.contains($0.draftID) }
    }

    // MARK: Panel visibility
    /// The close (x) button: hide the panel (ends nothing).
    var onClosePanel: () -> Void = {}
    /// Pointer is over the panel (holds the post-call auto-hide).
    var hovering = false

    var presentation: PillPresentation { present(state) }

    /// Two or more tasks in this call: show the list.
    var showsTaskList: Bool { state.tasks.count >= 2 }
    var selectedTask: TaskItem? { selectedTaskID.flatMap { id in state.tasks.first { $0.id == id } } }
}
