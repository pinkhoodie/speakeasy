import SwiftUI
import SpeakeasyCore

/// Observable model the SwiftUI panel renders. The controller owns all mutation.
@MainActor
final class VoicePanelModel: ObservableObject {
    static let defaultWidth: CGFloat = 400
    @Published var state = VoiceState()
    @Published var workExpanded = false
    @Published var captionExpanded = false
    /// Drafts whose body is expanded ("Show all"). Lives here, not in the card, so the panel re-measures and
    /// the card's Show less / Send row stays inside the window.
    @Published var expandedDraftIDs: Set<String> = []
    /// Status line text after the minimum-dwell debounce.
    @Published var shownStatus = ""
    @Published var shownTone: StatusTone = .plain
    @Published var busy = false
    /// Live captions of the conversation (Settings › General).
    @Published var showCaptions = true
    /// e.g. "⌃⌥M: tap to mute/unmute, hold to talk". Empty when no shortcut.
    @Published var muteShortcutHint = ""
    /// 0...1 audio level the orb follows (assistant voice while speaking, mic while listening).
    @Published var orbLevel: Double = 0
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

    /// The first-call tour is running in this call (shows a Skip tour button).
    @Published var tourActive = false
    var onSkipTour: () -> Void = {}

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
    /// The live "what it's looking at" image for a run (authenticated route; nil when none yet).
    var loadLiveImage: (String) async -> Data? = { _ in nil }
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

    // MARK: Image review cards
    /// Dismiss a review card (run id); its images stay inside the task.
    var onDismissReview: (String) -> Void = { _ in }
    /// Which image each review card shows (run id -> index into its images).
    @Published var reviewIndex: [String: Int] = [:]
    /// The review card shown large (run id); nil = the newest. The others are compact rows.
    @Published var focusedReviewID: String?
    /// Review cards to pin in the call panel (like drafts): most recent three.
    var pinnedReviews: [ImageReview] { state.pendingReviews }

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
