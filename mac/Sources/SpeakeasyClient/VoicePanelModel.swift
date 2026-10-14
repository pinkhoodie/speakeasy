import SwiftUI
import SpeakeasyCore

/// Observable model the SwiftUI panel renders. The controller owns all mutation.
@MainActor
public final class VoicePanelModel: ObservableObject {
    public init() {}
    public static let defaultWidth: CGFloat = 360
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

    /// Orb mode (was "slim"): the panel folds into a small orb with the mic and hang-up buttons. The orb's
    /// colour says what's going on (talking, working, something needs you, a failure). Remembered across
    /// calls under the old key, so an existing "slim" preference opens as the orb.
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

    // Listening mode (Mac). It lives beside the call, not in it: the strip shows only while no
    // call is open, and turning listening off starts the call.
    /// What the listening strip shows; nil hides it.
    @Published public var room: RoomPresentation?
    /// A one-off note about listening mode ("stopped when your Mac went to sleep…"), shown in the
    /// strip until dismissed or listening is turned on again.
    @Published public var roomNotice: String?
    /// The panel offers the Listening mode button (supported here, and no call is open).
    @Published public var roomOffered = false
    /// Why the button is disabled (e.g. the plugin needs an update); nil when it can be turned on.
    @Published public var roomBlocked: String?
    /// What it heard can still be asked about or discarded (on, or kept after it stopped by itself).
    @Published public var roomCanAsk = false
    /// e.g. "⌃⌥L: turn listening mode on or off". Empty when no shortcut.
    @Published public var roomShortcutHint = ""
    /// Turn listening mode on, or off into a call.
    public var onToggleRoom: () -> Void = {}
    /// Stop listening and drop what was heard, without a call.
    public var onDiscardRoom: () -> Void = {}
    /// Start the call that carries what was heard (turning listening off if it's on).
    public var onAskRoom: () -> Void = {}
    public var onDismissRoomNotice: () -> Void = {}

    public var onToggleMic: () -> Void = {}
    public var onStart: () -> Void = {}
    public var onEnd: () -> Void = {}
    public var onApproval: (String) -> Void = { _ in }
    public var onStopWork: () -> Void = {}
    public var onCloseWork: () -> Void = {}
    public var onToggleWork: () -> Void = {}
    public var onTogglePause: () -> Void = {}
    public var onStopTask: (String) -> Void = { _ in }
    /// Answer a task's question card (task id, picked option).
    public var onAnswer: (String, String) -> Void = { _, _ in }
    /// Answers already given, by task id, so a card stays settled after the tap (kept across launches).
    @Published public var answers: [String: String] = VoicePanelModel.loadAnswers() {
        didSet { VoicePanelModel.save(answers.suffix(200), key: VoicePanelModel.answersKey) }
    }
    /// Fetches a task picture (run id, card number) through the authenticated api.
    public var loadCardImage: (String, Int) async -> Data? = { _, _ in nil }
    public func questionActions(for taskID: String) -> QuestionActions {
        let runID = state.tasks.first(where: { $0.id == taskID })?.info.runID
        let load = loadCardImage
        return QuestionActions(onAnswer: { [weak self] text in
            self?.answers[taskID] = text
            self?.onAnswer(taskID, text)
        }, answered: answers[taskID], image: runID.map { run in { number in await load(run, number) } })
    }
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
    /// Tasks whose cards the user closed in the call panel (they stay in the task itself). Kept
    /// across launches: finished tasks come back with every call, and a closed card must stay closed.
    @Published public var dismissedViewTasks: Set<String> = Set(VoicePanelModel.loadList(VoicePanelModel.dismissedKey)) {
        didSet { UserDefaults.standard.set(Array(dismissedViewTasks.suffix(300)), forKey: VoicePanelModel.dismissedKey) }
    }
    public func dismissPinnedViews(_ taskID: String) { dismissedViewTasks.insert(taskID) }
    /// How long an unanswered question stays pinned. Past that it lives only in its task: a
    /// question from an old task must not greet you on every call.
    public static let questionPinnedFor: TimeInterval = 20 * 60
    static let dismissedKey = "speakeasy.dismissedCardTasks"
    static let answersKey = "speakeasy.questionAnswers"
    static func loadList(_ key: String) -> [String] { UserDefaults.standard.stringArray(forKey: key) ?? [] }
    static func loadAnswers() -> [String: String] {
        (UserDefaults.standard.dictionary(forKey: answersKey) as? [String: String]) ?? [:]
    }
    static func save(_ pairs: some Sequence<(key: String, value: String)>, key: String) {
        UserDefaults.standard.set(Dictionary(pairs.map { ($0.key, $0.value) }, uniquingKeysWith: { a, _ in a }), forKey: key)
    }
    /// The newest task with cards, shown in the call panel while it's fresh: a quick answer's card
    /// appears as it is spoken and stays until closed or until it's three minutes old.
    public var pinnedViews: (taskID: String, views: [ViewCard])? {
        // An unanswered question stays up (it's waiting on you); other cards fade after three minutes.
        if let asking = state.tasks.last(where: { t in t.info.views.contains { $0.kind == "question" }
                && answers[t.id] == nil && !dismissedViewTasks.contains(t.id) && t.info.isTerminal
                && t.info.updatedAt.map { Date().timeIntervalSince($0) < Self.questionPinnedFor } ?? false }) {
            return (asking.id, asking.info.views)
        }
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

    // MARK: Jobs: the board, the job view and the orb

    /// Width of the folded orb panel.
    public static let orbWidth: CGFloat = 132
    /// The panel's width right now: the orb when folded, else the user's width.
    public var displayWidth: CGFloat { showsSlim ? Self.orbWidth : panelWidth }
    /// The job open in the job view (a `Job.id`); nil shows the board.
    @Published public var selectedJobID: String?
    /// The board tile the arrow keys are on.
    @Published public var boardFocusID: String?
    /// Open a job (nil = back to the board). The client also tells the call which job is on screen.
    public var onOpenJob: (String?) -> Void = { _ in }
    /// "Tell it something": turn the mic on; what's said next goes to this job (job id).
    public var onTellJob: (String) -> Void = { _ in }
    /// The job waiting to hear from the user after "Tell it something".
    @Published public var tellingJobID: String?
    /// Jobs whose report text is expanded ("Full report").
    @Published public var expandedReports: Set<String> = []

    /// Every task grouped into jobs, newest first.
    public var jobs: [Job] {
        JobBoard.jobs(state.tasks, answered: Set(answers.keys), dismissed: dismissedViewTasks,
                      reviewing: Set(pinnedReviews.map(\.taskID)), now: state.now)
    }
    public var boardSections: JobBoard.Sections {
        JobBoard.sections(jobs, seen: Set(seenAnswers).union(dismissedViewTasks), now: state.now)
    }
    /// Answers the user opened (run ids, oldest first). Kept across launches, like closed cards:
    /// finished tasks come back with every call, and a read answer must stay read.
    @Published public var seenAnswers: [String] = VoicePanelModel.loadList(VoicePanelModel.seenAnswersKey) {
        didSet { UserDefaults.standard.set(Array(seenAnswers.suffix(300)), forKey: VoicePanelModel.seenAnswersKey) }
    }
    static let seenAnswersKey = "speakeasy.seenAnswers"
    /// Opening a job reads its answer: it moves from New answers to Done today.
    public func markAnswerSeen(_ job: Job) {
        guard let t = job.answerTask else { return }
        let key = JobBoard.seenKey(t)
        if !seenAnswers.contains(key) { seenAnswers.append(key) }
    }
    /// Answers finished today that the user hasn't opened.
    public var unreadAnswers: Int { boardSections.newAnswers.count }
    /// The orb's calm blue dot: answers to read and nothing more pressing.
    public var orbAnswerDot: Bool { orbState.showsAnswerDot(unread: unreadAnswers) }

    /// Why the orb has its colour, in a few words, for the header's second line: "1 needs you" next
    /// to amber, "2 new answers" next to the blue dot, "1 failed" next to red, "2 running" next to
    /// violet. nil when the orb is calm or listening: the live status shows instead.
    public var orbReason: (text: String, tone: StatusTone)? {
        let answers = unreadAnswers == 1 ? "1 new answer" : "\(unreadAnswers) new answers"
        switch orbState {
        case .failed(let needs):
            let failed = jobs.filter { $0.stage == .failed }.count
            return ("\(max(failed, 1)) failed" + (needs > 0 ? " · \(needs) need\(needs == 1 ? "s" : "") you" : ""), .error)
        case .needsYou(let n):
            return ("\(n) need\(n == 1 ? "s" : "") you", .attention)
        case .working:
            let running = boardSections.running.count
            let base = running == 1 ? "1 running" : "\(max(running, 1)) running"
            return (orbAnswerDot ? base + " · " + answers : base, .plain)
        case .idle, .talking:
            return orbAnswerDot ? (answers, .plain) : nil
        }
    }
    /// The header's second line: the orb's reason when it has one, else the live status.
    public var headerStatus: String { orbReason?.text ?? shownStatus }
    public var headerTone: StatusTone { orbReason?.tone ?? shownTone }
    public var selectedJob: Job? { selectedJobID.flatMap { id in jobs.first { $0.id == id } } }
    /// The board replaces the task list once there's more than one task, or anything needs you.
    public var showsBoard: Bool {
        guard !showsSlim, !state.workOnly, !state.tasks.isEmpty else { return false }
        return showsTaskList || jobs.contains { $0.stage.needsYou || $0.stage == .failed }
    }
    /// The mic is open and hearing the user right now.
    public var userTalking: Bool { presentation.mark == .listening && orbLevel > 0.08 }
    public var orbState: OrbState { OrbState.of(jobs: jobs, approvalPending: state.approval != nil, userTalking: userTalking) }

    /// The next job that needs you, other than `id` (the "Next:" link).
    public func nextJobNeedingYou(after id: String?) -> Job? {
        boardSections.needsYou.first { $0.id != id && $0.stage.needsYou }
    }

    /// Answer the job's waiting decision with option `number` (1-based). False when there's nothing to pick.
    @discardableResult
    public func pick(_ number: Int, in job: Job) -> Bool {
        guard let task = job.decisionTask, let decision = task.info.decision, answers[task.id] == nil,
              (1...decision.options.count).contains(number) else { return false }
        questionActions(for: task.id).onAnswer?(decision.options[number - 1].text)
        return true
    }

    /// Approve: answer with the recommended option, only when the plugin allowed it.
    public func approve(_ job: Job) {
        guard let d = job.decision, d.approvable, let r = d.recommended else { return }
        pick(r + 1, in: job)
    }

    /// Board and job-view keys: up/down move between tiles, Return opens, 1-4 picks an option on the
    /// focused decision (or the open job's). False lets the key go elsewhere.
    public func handleKey(code: UInt16, characters: String) -> Bool {
        if showsSlim { return false }
        // Escape: out of a job, back to the board.
        if code == 53 && selectedJobID != nil { onOpenJob(nil); return true }
        let digit = Int(characters).flatMap { (1...4).contains($0) ? $0 : nil }
        if workExpanded || state.workOnly {
            guard let job = selectedJob, let digit else { return false }
            return pick(digit, in: job)
        }
        guard showsBoard else { return false }
        let all = boardSections.all
        guard !all.isEmpty else { return false }
        let at = all.firstIndex { $0.id == boardFocusID }
        switch code {
        case 125: boardFocusID = all[min((at ?? -1) + 1, all.count - 1)].id; return true
        case 126: boardFocusID = all[max((at ?? all.count) - 1, 0)].id; return true
        case 36, 76:
            guard let at else { return false }
            onOpenJob(all[at].id); return true
        default:
            guard let digit else { return false }
            let target = at.map { all[$0] } ?? all.first { $0.stage == .needsYou(.decide) }
            return target.map { pick(digit, in: $0) } ?? false
        }
    }
}
