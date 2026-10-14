import Foundation

// Jobs: the board groups tasks into jobs (one piece of work that may span several requests,
// calls and days). The plugin names the job on each task (`job: {id, name}`); an older plugin
// sends nothing, and then every task is its own job named after the task.

/// The job a task belongs to, as the plugin reported it.
public struct JobRef: Equatable, Sendable {
    public var id: String
    public var name: String
    public init(id: String, name: String) { self.id = id; self.name = name }
    public init?(json: Any?) {
        guard let o = json as? [String: Any], let id = nonEmpty(o["id"]) else { return nil }
        self.id = id
        let name = nonEmpty(o["name"])?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        self.name = name.isEmpty ? "Job" : String(name.prefix(80))
    }
}

// MARK: - Decisions (question cards with context, options and a recommendation)

public struct DecisionTag: Equatable, Sendable {
    public var text: String
    /// good | warn | bad | neutral
    public var tone: String
    public init(text: String, tone: String = "neutral") { self.text = text; self.tone = tone }
}

public struct DecisionOption: Equatable, Sendable {
    public var text: String
    public var detail: String?
    public var tags: [DecisionTag]
    /// low | medium | high, when the plugin said so.
    public var risk: String?
    /// nil = not stated.
    public var reversible: Bool?
    /// Card image number for a picture option.
    public var image: Int?

    public init(text: String, detail: String? = nil, tags: [DecisionTag] = [], risk: String? = nil,
                reversible: Bool? = nil, image: Int? = nil) {
        self.text = text; self.detail = detail; self.tags = tags; self.risk = risk; self.reversible = reversible; self.image = image
    }

    /// A string (older plugins) or an object `{text, detail, tags, risk, reversible, image}`.
    public init?(json: JSONValue) {
        if case .string(let s) = json {
            let t = s.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !t.isEmpty else { return nil }
            self.init(text: t); return
        }
        guard let text = json["text"].string?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty else { return nil }
        let tags = json["tags"].array.prefix(4).compactMap { raw -> DecisionTag? in
            if let s = raw.string, case .string = raw { return DecisionTag(text: s) }
            guard let t = raw["text"].string else { return nil }
            return DecisionTag(text: String(t.prefix(40)), tone: raw["tone"].string ?? "neutral")
        }
        let risk = json["risk"].string.map { $0.lowercased() }
        self.init(text: text, detail: json["detail"].string, tags: Array(tags),
                  risk: ["low", "medium", "high"].contains(risk ?? "") ? risk : nil,
                  reversible: json["reversible"].bool, image: json["image"].double.map { Int($0) })
    }
}

/// A `question` view read as a decision brief. Every field beyond the question and options is optional.
public struct Decision: Equatable, Sendable {
    public var question: String
    public var context: String?
    public var why: String?
    public var affects: String?
    public var options: [DecisionOption]
    public var recommended: Int?
    /// One-tap Approve is allowed (the plugin decided; never inferred here).
    public var approvable: Bool

    public init(question: String, context: String? = nil, why: String? = nil, affects: String? = nil,
                options: [DecisionOption], recommended: Int? = nil, approvable: Bool = false) {
        self.question = question; self.context = context; self.why = why; self.affects = affects
        self.options = options; self.recommended = recommended; self.approvable = approvable
    }

    public init?(card: ViewCard) {
        guard card.kind == "question", let q = card["question"].string else { return nil }
        let options = card["options"].array.prefix(4).compactMap(DecisionOption.init(json:))
        guard !options.isEmpty else { return nil }
        let rec = card["recommended"].double.map { Int($0) }
        self.init(question: q, context: card["context"].string, why: card["why"].string, affects: card["affects"].string,
                  options: Array(options), recommended: rec.flatMap { (0..<options.count).contains($0) ? $0 : nil },
                  approvable: card["approvable"].bool ?? false)
        // Approve needs something to approve.
        if recommended == nil { approvable = false }
    }

    public var recommendedOption: DecisionOption? { recommended.map { options[$0] } }
}

public extension WorkInfo {
    /// The decision this task's answer asks for, if any.
    var decision: Decision? { views.lazy.compactMap(Decision.init(card:)).first }
}

// MARK: - Jobs and the board

public struct Job: Equatable, Sendable, Identifiable {
    public enum Need: String, Equatable, Sendable {
        case decide, approve, review
        /// The pill on the board tile.
        public var label: String {
            switch self { case .decide: return "Decide"; case .approve: return "Approve"; case .review: return "Review" }
        }
    }
    public enum Stage: Equatable, Sendable {
        case needsYou(Need), failed, running, done
        public var needsYou: Bool { if case .needsYou = self { return true }; return false }
    }

    public var id: String
    public var name: String
    /// Oldest first, like `VoiceState.tasks`.
    public var tasks: [TaskItem]
    public var stage: Stage
    /// The task whose question waits on you (stage `.needsYou(.decide)`).
    public var decisionTaskID: String?

    public var newest: TaskItem { tasks[tasks.count - 1] }
    public var updated: Date? { tasks.compactMap { $0.info.updatedAt ?? $0.info.updated }.max() }
    public var decisionTask: TaskItem? { decisionTaskID.flatMap { id in tasks.first { $0.id == id } } }
    public var decision: Decision? { decisionTask?.info.decision }
    /// The task the job is busy with right now (newest still running).
    public var activeTask: TaskItem? { tasks.last(where: \.isActive) }
}

public enum JobBoard {
    /// How long an unanswered decision stays on the board.
    public static let decisionKeptFor: TimeInterval = 24 * 3600

    /// Groups tasks into jobs, newest job first.
    /// - answered: task ids whose question was answered here.
    /// - dismissed: task ids whose cards the user closed.
    /// - reviewing: task ids with a design review still waiting.
    public static func jobs(_ tasks: [TaskItem], answered: Set<String> = [], dismissed: Set<String> = [],
                            reviewing: Set<String> = [], now: Date = Date()) -> [Job] {
        var order: [String] = []
        var grouped: [String: [TaskItem]] = [:]
        var names: [String: String] = [:]
        for task in tasks {
            let key = task.info.job.map { "job:" + $0.id } ?? "task:" + task.id
            if grouped[key] == nil { order.append(key); names[key] = task.info.job?.name ?? task.name }
            grouped[key, default: []].append(task)
        }
        var jobs: [(job: Job, last: Int)] = []
        for key in order {
            let items = grouped[key] ?? []
            let last = items.compactMap { t in tasks.lastIndex { $0.id == t.id } }.max() ?? 0
            var decisionID: String?
            var need: Job.Need?
            for t in items.reversed() {
                if t.info.status == "waiting_for_approval" { need = need ?? .approve; continue }
                if !t.info.pendingDrafts.isEmpty || reviewing.contains(t.id) { if need != .decide && need != .approve { need = .review }; continue }
                if decisionID == nil, isDecisionPending(t, answered: answered, dismissed: dismissed, now: now) {
                    decisionID = t.id
                    if need == nil || need == .review { need = .decide }
                }
            }
            if decisionID != nil && need != .approve { need = .decide }
            let newest = items[items.count - 1]
            let stage: Job.Stage
            if let need { stage = .needsYou(need) }
            else if items.contains(where: \.isActive) { stage = .running }
            else if newest.info.status == "failed" { stage = .failed }
            else { stage = .done }
            jobs.append((Job(id: key, name: names[key] ?? newest.name, tasks: items, stage: stage, decisionTaskID: decisionID), last))
        }
        return jobs.sorted { $0.last > $1.last }.map(\.job)
    }

    static func isDecisionPending(_ t: TaskItem, answered: Set<String>, dismissed: Set<String>, now: Date) -> Bool {
        guard t.info.isTerminal, t.info.decision != nil, !answered.contains(t.id), !dismissed.contains(t.id),
              let at = t.info.updatedAt ?? t.info.updated else { return false }
        return now.timeIntervalSince(at) < decisionKeptFor
    }

    public struct Sections: Equatable, Sendable {
        /// Decisions first, then approvals, reviews, and failures.
        public var needsYou: [Job]
        public var running: [Job]
        public var doneToday: [Job]
        /// Board order, for the arrow keys.
        public var all: [Job] { needsYou + running + doneToday }
        public var isEmpty: Bool { all.isEmpty }
    }

    public static func sections(_ jobs: [Job], now: Date = Date(), calendar: Calendar = .current) -> Sections {
        func rank(_ j: Job) -> Int {
            switch j.stage {
            case .needsYou(.decide): return 0
            case .needsYou(.approve): return 1
            case .needsYou(.review): return 2
            default: return 3
            }
        }
        let needs = jobs.filter { $0.stage.needsYou || $0.stage == .failed }
            .enumerated().sorted { (rank($0.element), $0.offset) < (rank($1.element), $1.offset) }.map(\.element)
        let running = jobs.filter { $0.stage == .running }
        let done = jobs.filter { j in
            j.stage == .done && (j.updated.map { calendar.isDate($0, inSameDayAs: now) } ?? true)
        }
        return Sections(needsYou: needs, running: running, doneToday: done)
    }

    /// Plan progress for a running job: (steps done, total), or nil without a plan.
    public static func progress(_ info: WorkInfo) -> (done: Int, total: Int)? {
        guard !info.plan.isEmpty else { return nil }
        if info.isTerminal && info.status == "completed" { return (info.plan.count, info.plan.count) }
        return (min(info.milestones.count, info.plan.count - 1), info.plan.count)
    }
}

// MARK: - The orb

/// What the folded panel's orb shows.
public enum OrbState: Equatable, Sendable {
    case idle, talking, working
    case needsYou(Int)
    case failed(needs: Int)

    /// - userTalking: the mic is open and hearing the user.
    public static func of(jobs: [Job], approvalPending: Bool = false, userTalking: Bool) -> OrbState {
        var needs = jobs.filter(\.stage.needsYou).count
        if approvalPending && !jobs.contains(where: { $0.stage == .needsYou(.approve) }) { needs += 1 }
        if jobs.contains(where: { $0.stage == .failed }) { return .failed(needs: needs) }
        if needs > 0 { return .needsYou(needs) }
        if userTalking { return .talking }
        if jobs.contains(where: { $0.stage == .running }) { return .working }
        return .idle
    }

    public var badge: Int? {
        switch self {
        case .needsYou(let n): return n
        case .failed(let n): return n > 0 ? n : nil
        default: return nil
        }
    }

    /// VoiceOver and tooltip text.
    public var label: String {
        switch self {
        case .idle: return "Nothing running"
        case .talking: return "Listening to you"
        case .working: return "Working"
        case .needsYou(let n): return n == 1 ? "1 thing needs you" : "\(n) things need you"
        case .failed: return "A task failed"
        }
    }
}
