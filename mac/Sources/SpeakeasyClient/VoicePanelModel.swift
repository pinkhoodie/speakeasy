import SwiftUI
import SpeakeasyCore

/// Observable model the SwiftUI panel renders. The controller owns all mutation.
@MainActor
public final class VoicePanelModel: ObservableObject {
    public init() {}
    public static let defaultWidth: CGFloat = 400
    @Published public var state = VoiceState()
    @Published public var workExpanded = false
    @Published public var captionExpanded = false
    /// Drafts whose body is expanded ("Show all"). Lives here, not in the card, so the panel re-measures and
    /// the card's Show less / Send row stays inside the window.
    @Published public var expandedDraftIDs: Set<String> = []
    /// Status line text after the minimum-dwell debounce.
    @Published public var shownStatus = ""
    @Published public var shownTone: StatusTone = .plain
    @Published public var busy = false
    /// Live captions of the conversation (Settings › General).
    @Published public var showCaptions = true
    /// e.g. "⌃⌥M: tap to mute/unmute, hold to talk". Empty when no shortcut.
    @Published public var muteShortcutHint = ""
    /// 0...1 audio level the orb follows (assistant voice while speaking, mic while listening).
    @Published public var orbLevel: Double = 0
    /// the user's panel size: width, plus extra height given to the transcript and
    /// Work scroll areas. Set by dragging the panel's right/bottom edge.
    @Published public var panelWidth: CGFloat = VoicePanelModel.defaultWidth
    @Published public var extraHeight: CGFloat = 0

    /// Slim mode: just the header row (status, pause, mic, end) plus a one-line task
    /// summary. Approvals and pending email drafts still show. Remembered across calls.
    @Published public var slim: Bool = UserDefaults.standard.bool(forKey: VoicePanelModel.slimKey) {
        didSet { UserDefaults.standard.set(slim, forKey: VoicePanelModel.slimKey) }
    }
    public nonisolated static let slimKey = "panel.slim"
    public var onToggleSlim: () -> Void = {}
    /// Slim only applies to an open call; a finished or work-only panel is always full.
    public var showsSlim: Bool { slim && !state.workOnly && state.connection.isOpen }

    /// The first-call tour is running in this call (shows a Skip tour button).
    @Published public var tourActive = false
    public var onSkipTour: () -> Void = {}

    public var onToggleMic: () -> Void = {}
    public var onStart: () -> Void = {}
    public var onEnd: () -> Void = {}
    public var onApproval: (String) -> Void = { _ in }
    public var onStopWork: () -> Void = {}
    public var onCloseWork: () -> Void = {}
    public var onToggleWork: () -> Void = {}
    public var onTogglePause: () -> Void = {}
    public var onStopTask: (String) -> Void = { _ in }
    /// nil shows the task list; an id opens that task.
    public var onSelectTask: (String?) -> Void = { _ in }
    /// Clear finished tasks (run ids) from the list.
    public var onDismissTasks: ([String]) -> Void = { _ in }
    /// Exact-run, authenticated image load through the api; no retailer request from the app.
    public var loadProductImage: (String, Int) async -> Data? = { _, _ in nil }
    /// The live "what it's looking at" image for a run (authenticated route; nil when none yet).
    public var loadLiveImage: (String) async -> Data? = { _ in nil }
    /// Task opened from the list (Work view), nil = list / current task.
    @Published public var selectedTaskID: String?
    /// e.g. "⌃⌥P". Empty when no shortcut.
    @Published public var pauseShortcutHint = ""

    // MARK: Email drafts
    /// Approve/Deny/Revise on a draft (draft as shown, action, revise instructions).
    public var onDraftAction: (EmailDraft, DraftAction, String?) -> Void = { _, _, _ in }
    /// Drafts with a request in flight (buttons disabled).
    @Published public var draftBusy: Set<String> = []
    /// Optimistic status after a tap, keyed "draftID|sha256" so a new version starts clean.
    @Published public var draftLocalStatus: [String: EmailDraft.Status] = [:]
    /// Per-draft notice, e.g. after a 409 "The draft changed — review it again".
    @Published public var draftNotice: [String: String] = [:]

    public func displayedStatus(of draft: EmailDraft) -> EmailDraft.Status {
        guard draft.status == .pending else { return draft.status }   // the server has moved on
        return draftLocalStatus["\(draft.draftID)|\(draft.sha256)"] ?? .pending
    }

    /// Pending drafts to pin in the call panel (like approvals).
    public var pinnedDrafts: [EmailDraft] {
        state.pendingDrafts.filter { displayedStatus(of: $0) == .pending || draftBusy.contains($0.draftID) }
    }

    // MARK: Answer cards (price, weather, game…)
    /// Tasks whose cards the user closed in the call panel (they stay in the task itself).
    @Published public var dismissedViewTasks: Set<String> = []
    public func dismissPinnedViews(_ taskID: String) { dismissedViewTasks.insert(taskID) }
    /// The newest task with cards, shown in the call panel while it's fresh: a quick answer's card
    /// appears as it is spoken and stays until closed or until it's three minutes old.
    public var pinnedViews: (taskID: String, views: [ViewCard])? {
        guard let task = state.tasks.last(where: { !$0.info.views.isEmpty && !dismissedViewTasks.contains($0.id) }) else { return nil }
        if let at = task.info.updatedAt, Date().timeIntervalSince(at) > 180 { return nil }
        return (task.id, task.info.views)
    }

    // MARK: Image review cards
    /// Dismiss a review card (run id); its images stay inside the task.
    public var onDismissReview: (String) -> Void = { _ in }
    /// Which image each review card shows (run id -> index into its images).
    @Published public var reviewIndex: [String: Int] = [:]
    /// The review card shown large (run id); nil = the newest. The others are compact rows.
    @Published public var focusedReviewID: String?
    /// Review cards to pin in the call panel (like drafts): most recent three.
    public var pinnedReviews: [ImageReview] { state.pendingReviews }

    /// The open review card when it has more than one image and is on screen.
    public var steppableReview: ImageReview? {
        guard !(workExpanded || state.workOnly),
              let open = ImageReviewLayout.focused(pinnedReviews, picked: focusedReviewID),
              let review = pinnedReviews.first(where: { $0.runID == open }), review.images.count > 1 else { return nil }
        return review
    }

    /// Arrow keys on the panel: step the open review card's images. False when there's nothing to step
    /// (no review showing, one image, or the work view covers the cards) so the key goes elsewhere.
    @discardableResult
    public func stepOpenReview(by delta: Int) -> Bool {
        guard let review = steppableReview else { return false }
        let open = review.runID
        let current = min(reviewIndex[open] ?? 0, review.images.count - 1)
        reviewIndex[open] = ImageReviewLayout.step(current, by: delta, count: review.images.count)
        return true
    }

    // MARK: Panel visibility
    /// The close (x) button: hide the panel (ends nothing).
    public var onClosePanel: () -> Void = {}
    /// Pointer is over the panel (holds the post-call auto-hide).
    public var hovering = false

    public var presentation: PillPresentation { present(state) }

    /// Two or more tasks in this call: show the list.
    public var showsTaskList: Bool { state.tasks.count >= 2 }
    public var selectedTask: TaskItem? { selectedTaskID.flatMap { id in state.tasks.first { $0.id == id } } }
}
