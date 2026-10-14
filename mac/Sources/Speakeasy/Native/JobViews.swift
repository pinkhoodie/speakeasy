import AppKit
import SwiftUI
import SpeakeasyCore
import SpeakeasyClient

// The orb, the job board and the job view (docs/plans/2026-10-14-002-feat-job-view-plan.md).

// MARK: - Style

enum JobStyle {
    static let blue = Color(red: 0.25, green: 0.39, blue: 0.79)        // #3f63c9 Approve
    static let blueInk = Color(red: 0.62, green: 0.71, blue: 1.0)      // #9fb6ff
    static let amber = Color(red: 0.94, green: 0.74, blue: 0.36)       // #f0bd5c
    static let green = Color(red: 0.50, green: 0.85, blue: 0.60)       // #7fd99a
    static let red = Color(red: 0.94, green: 0.54, blue: 0.48)         // #f08a7a

    enum Pill { case amber, blue, green, red, grey }
    static func pillColors(_ p: Pill) -> (Color, Color) {
        switch p {
        case .amber: return (amber.opacity(0.20), amber)
        case .blue: return (blueInk.opacity(0.18), blueInk)
        case .green: return (green.opacity(0.18), green)
        case .red: return (red.opacity(0.20), red)
        case .grey: return (Color.primary.opacity(0.10), Color.secondary)
        }
    }
    static func pill(tone: String) -> Pill {
        switch tone { case "good": return .green; case "warn": return .amber; case "bad": return .red; case "info": return .blue; default: return .grey }
    }

    /// Room a scrolling job area can take without pushing the panel off the screen.
    static var scrollCap: CGFloat {
        let screen = NSScreen.main?.visibleFrame.height ?? 800
        return max(260, screen - 260)
    }
}

struct JobPill: View {
    var text: String
    var style: JobStyle.Pill
    var body: some View {
        let (bg, fg) = JobStyle.pillColors(style)
        Text(text).font(.system(size: 10, weight: .semibold)).lineLimit(1)
            .padding(.horizontal, 7).padding(.vertical, 2)
            .background(Capsule().fill(bg)).foregroundStyle(fg)
            .fixedSize()
    }
}

struct SectionLabel: View {
    var text: String
    var body: some View {
        Text(text.uppercased()).font(.system(size: 9.5, weight: .semibold)).tracking(0.7).foregroundStyle(.tertiary)
    }
}

/// A full-width button in a tile (Approve / See options).
struct TileButton: View {
    var title: String
    var primary = false
    var action: () -> Void
    @State private var hover = false
    var body: some View {
        Button(action: action) {
            Text(title).font(.system(size: 11.5, weight: .semibold)).lineLimit(1)
                .frame(maxWidth: .infinity).frame(height: 26)
                .foregroundStyle(primary ? Color.white : Color.primary)
                .background(RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(primary ? JobStyle.blue.opacity(hover ? 1 : 0.92) : Color.primary.opacity(hover ? 0.16 : 0.10)))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
    }
}

/// Wraps children onto new lines (done chips, tag pills).
struct WrapLayout: Layout {
    var spacing: CGFloat = 5
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? 300
        var x: CGFloat = 0, y: CGFloat = 0, row: CGFloat = 0, widest: CGFloat = 0
        for s in subviews {
            let size = s.sizeThatFits(.unspecified)
            let w = min(size.width, width)
            if x > 0 && x + w > width { y += row + spacing; x = 0; row = 0 }
            x += w + spacing; row = max(row, size.height); widest = max(widest, x - spacing)
        }
        return CGSize(width: proposal.width ?? widest, height: y + row)
    }
    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, row: CGFloat = 0
        for s in subviews {
            let size = s.sizeThatFits(.unspecified)
            let w = min(size.width, bounds.width)
            if x > bounds.minX && x + w > bounds.maxX { y += row + spacing; x = bounds.minX; row = 0 }
            s.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(width: w, height: size.height))
            x += w + spacing; row = max(row, size.height)
        }
    }
}

// MARK: - The orb

/// The one orb of a call: the folded panel, the panel header, the board and the job view all draw
/// this, so a colour means the same everywhere. Grey idle, blue the user talking, violet working,
/// amber something needs you (with a count), red a failure; a calm blue dot marks unread answers.
struct JobOrb: View {
    var state: OrbState
    var size: CGFloat = 40
    /// 0...1: the user's mic while they talk, the assistant's voice while it speaks.
    var level: Double = 0
    /// What the call is doing (speaking moves the orb a little, muted dims it). nil = no call detail.
    var mark: PillPresentation.Mark? = nil
    /// Answers finished and not opened yet: a small blue dot when nothing more pressing shows.
    var unreadAnswers: Int = 0
    /// Where this orb sits ("folded", "header"); the smoke test reads what each one drew.
    var placement = ""
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    static func colors(_ state: OrbState) -> [Color] {
        switch state {
        case .idle: return [Color(red: 0.86, green: 0.87, blue: 0.91), Color(red: 0.52, green: 0.55, blue: 0.64), Color(red: 0.28, green: 0.30, blue: 0.37)]
        case .talking: return [Color(red: 0.86, green: 0.94, blue: 1.0), Color(red: 0.36, green: 0.62, blue: 1.0), Color(red: 0.12, green: 0.31, blue: 0.70)]
        case .working: return [Color(red: 0.80, green: 0.74, blue: 1.0), Color(red: 0.45, green: 0.45, blue: 0.98), Color(red: 0.22, green: 0.20, blue: 0.62)]
        case .needsYou: return [Color(red: 1.0, green: 0.90, blue: 0.69), Color(red: 0.94, green: 0.65, blue: 0.19), Color(red: 0.60, green: 0.36, blue: 0.05)]
        case .failed: return [Color(red: 1.0, green: 0.74, blue: 0.68), Color(red: 0.90, green: 0.34, blue: 0.29), Color(red: 0.54, green: 0.12, blue: 0.09)]
        }
    }
    static let answerBlue = Color(red: 0.37, green: 0.61, blue: 0.94)

    private var dim: Bool { mark == .muted || mark == .idle }
    private var answerDot: Bool { state.showsAnswerDot(unread: unreadAnswers) }
    private var voice: CGFloat { CGFloat(min(max(level, 0), 1)) }

    var body: some View {
        Group {
            if reduceMotion { orb(scale: 1, glow: 0.45) }
            else {
                switch state {
                case .talking:
                    // Breathing: a slow swell, lifted by the voice.
                    TimelineView(.animation(minimumInterval: 1 / 30)) { ctx in
                        let t = ctx.date.timeIntervalSinceReferenceDate
                        let breath = (sin(t * 2.4) + 1) / 2
                        orb(scale: 1 + 0.05 * breath + 0.08 * voice, glow: 0.45 + 0.35 * breath)
                    }
                case _ where mark == .connecting:
                    TimelineView(.animation(minimumInterval: 1 / 30)) { ctx in
                        let t = ctx.date.timeIntervalSinceReferenceDate
                        orb(scale: 0.92 + 0.06 * CGFloat((sin(t * 3.2) + 1) / 2), glow: 0.3)
                    }
                case .working:
                    // Soft pulse of the glow only; the orb keeps its size (it still lifts a little with the voice).
                    TimelineView(.animation(minimumInterval: 1 / 20)) { ctx in
                        let t = ctx.date.timeIntervalSinceReferenceDate
                        orb(scale: 1 + speakingLift, glow: 0.25 + 0.4 * (sin(t * 1.6) + 1) / 2)
                    }
                default:
                    // The assistant speaking moves the orb a little, in whatever colour it is.
                    orb(scale: 1 + speakingLift, glow: (state == .idle ? 0.1 : 0.55) + 0.4 * Double(speakingLift * 8))
                        .animation(.easeOut(duration: 0.12), value: speakingLift)
                }
            }
        }
        .saturation(dim ? 0.25 : 1)
        .opacity(dim ? 0.7 : 1)
        .frame(width: size + 8, height: size + 8)
        .overlay(alignment: .topTrailing) {
            if case .needsYou(let n) = state {
                // The count shows only when something needs you.
                Text("\(n)").font(.system(size: size < 34 ? 9.5 : 10, weight: .bold)).monospacedDigit()
                    .foregroundStyle(Color(red: 0.6, green: 0.36, blue: 0.05))
                    .padding(.horizontal, 4).frame(minWidth: 15, minHeight: 15)
                    .background(Capsule().fill(Color.white))
                    .offset(x: 1, y: -1)
            } else if answerDot {
                Circle().fill(Self.answerBlue).frame(width: 9, height: 9)
                    .overlay(Circle().strokeBorder(Color.white.opacity(0.85), lineWidth: 1.2))
                    .offset(x: -2, y: 2)
                    .accessibilityHidden(true)
            }
        }
        .overlay(alignment: .bottomTrailing) {
            if mark == .muted {
                Image(systemName: "mic.slash.fill").font(.system(size: 8, weight: .bold))
                    .foregroundStyle(Tokens.amberInk).padding(3)
                    .background(Circle().fill(Tokens.amber))
                    .offset(x: -1, y: -1)
            }
        }
        .accessibilityElement()
        .accessibilityLabel(accessibilityText)
        .onAppear { Self.drawn[placement] = Drawn(state: state, answerDot: answerDot) }
        .onChange(of: Drawn(state: state, answerDot: answerDot)) { _, now in Self.drawn[placement] = now }
    }

    struct Drawn: Equatable { var state: OrbState; var answerDot: Bool }
    /// What each placed orb last drew (panel smoke checks the header and the folded orb agree).
    @MainActor static var drawn: [String: Drawn] = [:]

    private var speakingLift: CGFloat { !reduceMotion && mark == .speaking ? 0.1 * voice : 0 }

    private var accessibilityText: String {
        guard answerDot else { return state.label }
        return state.label + (unreadAnswers == 1 ? ", 1 new answer" : ", \(unreadAnswers) new answers")
    }

    private func orb(scale: CGFloat, glow: Double) -> some View {
        let colors = Self.colors(state)
        return ZStack {
            Circle().fill(colors[1].opacity(min(glow, 0.95))).frame(width: size, height: size).blur(radius: size * 0.22)
            Circle()
                .fill(RadialGradient(colors: colors, center: UnitPoint(x: 0.35, y: 0.3), startRadius: 1, endRadius: size * 0.62))
                .overlay(Circle().strokeBorder(.white.opacity(0.2), lineWidth: 0.5))
                .frame(width: size, height: size)
        }
        .scaleEffect(scale)
    }
}

/// The folded panel: the orb, plus the mic and hang-up so the call stays in reach.
struct OrbBar: View {
    @ObservedObject var model: VoicePanelModel
    var body: some View {
        let p = model.presentation
        HStack(spacing: 6) {
            Button(action: model.onToggleSlim) {
                JobOrb(state: model.orbState, size: 36, level: model.orbLevel, mark: model.presentation.mark,
                       unreadAnswers: model.unreadAnswers, placement: "folded").contentShape(Circle())
            }
            .buttonStyle(.plain)
            .help("\(model.orbState.label). Open your jobs")
            .accessibilityLabel("\(model.orbState.label). Open your jobs")
            if model.state.connection != .paused {
                MicButton(muted: p.micMuted, enabled: p.micEnabled, shortcutHint: model.muteShortcutHint, action: model.onToggleMic)
                    .fixedSize()
            } else if let label = p.pauseLabel {
                PauseButton(label: label, enabled: p.pauseEnabled, shortcutHint: model.pauseShortcutHint, action: model.onTogglePause)
                    .fixedSize()
            }
            EndButton(action: model.onEnd).fixedSize()
        }
        .padding(.horizontal, 8).padding(.vertical, 6)
        .frame(width: VoicePanelModel.orbWidth)
    }
}

// MARK: - Board

struct JobBoardView: View {
    @ObservedObject var model: VoicePanelModel

    /// Where each tile sits in the scrolling content, so the keyboard focus scrolls into view.
    @State private var tileFrames: [String: CGRect] = [:]

    /// The board's scroll room: what the screen allows, less the transcript above it when open.
    static func scrollHeight(_ model: VoicePanelModel) -> CGFloat {
        let transcript: CGFloat = model.captionExpanded ? 150 + model.extraHeight + 24 : 56
        return min(max(200, JobStyle.scrollCap - transcript), 520 + model.extraHeight)
    }

    var body: some View {
        let sections = model.boardSections
        PanelScrollView(maxHeight: Self.scrollHeight(model), reveal: model.boardFocusID.flatMap { tileFrames[$0] }) {
            VStack(alignment: .leading, spacing: 7) {
                Text(summary(sections)).font(.system(size: 12.5, weight: .semibold)).foregroundStyle(.secondary)
                    .padding(.bottom, 1)
                if !sections.needsYou.isEmpty {
                    SectionLabel(text: "Needs you")
                    ForEach(sections.needsYou) { job in tile(job) }
                }
                if !sections.newAnswers.isEmpty {
                    SectionLabel(text: "New answers").padding(.top, sections.needsYou.isEmpty ? 0 : 4)
                    ForEach(sections.newAnswers) { job in
                        AnswerTile(job: job, focused: model.boardFocusID == job.id) { model.onOpenJob(job.id) }
                            .modifier(TileFrame(id: job.id))
                    }
                }
                if !sections.running.isEmpty {
                    SectionLabel(text: "Running").padding(.top, sections.needsYou.isEmpty && sections.newAnswers.isEmpty ? 0 : 4)
                    ForEach(sections.running) { job in tile(job) }
                }
                if !sections.doneToday.isEmpty {
                    SectionLabel(text: "Done today").padding(.top, sections.needsYou.isEmpty && sections.newAnswers.isEmpty && sections.running.isEmpty ? 0 : 4)
                    WrapLayout(spacing: 5) {
                        ForEach(sections.doneToday) { job in
                            DoneChip(job: job, focused: model.boardFocusID == job.id) { model.onOpenJob(job.id) }
                                .modifier(TileFrame(id: job.id))
                        }
                    }
                }
                Text(hint(sections)).font(.system(size: 10)).foregroundStyle(.tertiary).padding(.top, 3)
            }
            .padding(.trailing, 6)
            .frame(maxWidth: .infinity, alignment: .leading)
            .coordinateSpace(name: "board")
            .onPreferenceChange(TileFrameKey.self) { frames in
                MainActor.assumeIsolated { if frames != tileFrames { tileFrames = frames } }
            }
        }
    }

    private func summary(_ s: JobBoard.Sections) -> String {
        var parts: [String] = []
        if !s.needsYou.isEmpty { parts.append("\(s.needsYou.count) need\(s.needsYou.count == 1 ? "s" : "") you") }
        if !s.newAnswers.isEmpty { parts.append(s.newAnswers.count == 1 ? "1 new answer" : "\(s.newAnswers.count) new answers") }
        if !s.running.isEmpty { parts.append("\(s.running.count) running") }
        if !s.doneToday.isEmpty { parts.append("\(s.doneToday.count) done today") }
        return parts.joined(separator: " · ")
    }

    private func hint(_ s: JobBoard.Sections) -> String {
        let pick = s.needsYou.contains { $0.stage == .needsYou(.decide) } ? " · 1–3 pick" : ""
        return "↑↓ move · ↩ open\(pick) · esc fold into the orb"
    }

    @ViewBuilder private func tile(_ job: Job) -> some View {
        JobTile(job: job, model: model, focused: model.boardFocusID == job.id)
            .modifier(TileFrame(id: job.id))
    }
}

struct TileFrameKey: PreferenceKey {
    static let defaultValue: [String: CGRect] = [:]
    static func reduce(value: inout [String: CGRect], nextValue: () -> [String: CGRect]) {
        value.merge(nextValue()) { $1 }
    }
}

/// Reports a tile's frame in the board's scrolling content.
struct TileFrame: ViewModifier {
    var id: String
    func body(content: Content) -> some View {
        content.background(GeometryReader { g in
            Color.clear.preference(key: TileFrameKey.self, value: [id: g.frame(in: .named("board"))])
        })
    }
}

/// A finished answer the user hasn't opened: what it was about, the answer in one line, and a
/// small picture of it when the result has a card worth a glance.
struct AnswerTile: View {
    var job: Job
    var focused: Bool
    var action: () -> Void
    @State private var hover = false

    var body: some View {
        let info = (job.answerTask ?? job.newest).info
        let glance = JobAnswer.glanceCard(info)
        Button(action: action) {
            HStack(alignment: .center, spacing: 10) {
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) {
                        Circle().fill(JobOrb.answerBlue).frame(width: 6, height: 6)
                        Text(job.name).font(.system(size: 13, weight: .semibold)).lineLimit(1)
                    }
                    if let headline = JobAnswer.headline(info) {
                        Text(headline).font(.system(size: 11.5)).foregroundStyle(.secondary).lineLimit(2)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Text("Read ›").font(.system(size: 11, weight: .semibold)).foregroundStyle(JobStyle.blueInk)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                if let glance {
                    AnswerGlance(glance)
                        .fixedSize()
                        .padding(.horizontal, 8).padding(.vertical, 6)
                        .background(RoundedRectangle(cornerRadius: 9, style: .continuous).fill(Color.primary.opacity(0.06)))
                }
            }
            .padding(.horizontal, 11).padding(.vertical, 9)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(JobStyle.blueInk.opacity(hover ? 0.12 : 0.08)))
            .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous)
                .strokeBorder(focused ? JobStyle.blueInk : JobStyle.blueInk.opacity(0.28), lineWidth: focused ? 1.5 : 0.75))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
        .help("Read \(job.name)")
        .accessibilityLabel("New answer: \(job.name). \(JobAnswer.headline(info) ?? "")")
    }
}

struct DoneChip: View {
    var job: Job
    var focused: Bool
    var action: () -> Void
    @State private var hover = false
    var body: some View {
        Button(action: action) {
            HStack(spacing: 4) {
                Image(systemName: job.newest.info.status == "completed" ? "checkmark" : "stop.fill")
                    .font(.system(size: 8, weight: .bold)).foregroundStyle(JobStyle.green.opacity(0.9))
                Text(job.name).font(.system(size: 11)).lineLimit(1)
            }
            .padding(.horizontal, 8).padding(.vertical, 4)
            .background(Capsule().fill(Color.primary.opacity(hover ? 0.13 : 0.07)))
            .overlay(Capsule().strokeBorder(focused ? JobStyle.blueInk : .clear, lineWidth: 1.2))
            .foregroundStyle(.primary.opacity(0.8))
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
        .help("Open \(job.name)")
    }
}

struct JobTile: View {
    var job: Job
    @ObservedObject var model: VoicePanelModel
    var focused: Bool
    @State private var hover = false

    var body: some View {
        let needs = job.stage.needsYou
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Text(job.name).font(.system(size: 13, weight: .semibold)).lineLimit(1)
                Spacer(minLength: 4)
                pill
            }
            content
        }
        .padding(.horizontal, 11).padding(.vertical, 9)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous)
            .fill(needs ? Tokens.amber.opacity(hover ? 0.16 : 0.11) : Color.primary.opacity(hover ? 0.09 : 0.06)))
        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous)
            .strokeBorder(focused ? JobStyle.blueInk : (needs ? Tokens.amber.opacity(0.42) : Color.primary.opacity(0.07)),
                          lineWidth: focused ? 1.5 : 0.75))
        .contentShape(Rectangle())
        .onTapGesture { model.onOpenJob(job.id) }
        .onHover { hover = $0 }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(job.name)
    }

    @ViewBuilder private var pill: some View {
        switch job.stage {
        case .needsYou(let need): JobPill(text: need.label, style: .amber)
        case .failed: JobPill(text: "Failed", style: .red)
        case .running:
            if let p = JobBoard.progress(job.activeTask?.info ?? job.newest.info) {
                JobPill(text: "\(p.done + 1) of \(p.total)", style: .blue)
            } else { JobPill(text: "Running", style: .blue) }
        case .done: JobPill(text: "Done", style: .green)
        }
    }

    @ViewBuilder private var content: some View {
        switch job.stage {
        case .needsYou(.decide):
            if let d = job.decision {
                if let rec = d.recommendedOption {
                    // The question is one click away; the tile leads with what to do.
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text("RECOMMENDED").font(.system(size: 9, weight: .semibold)).tracking(0.6).foregroundStyle(.tertiary)
                            .fixedSize()
                        Text(rec.text).font(.system(size: 12, weight: .medium)).lineLimit(2)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .help(d.question)
                } else {
                    Text(d.question).font(.system(size: 11.5)).foregroundStyle(.secondary).lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                }
                HStack(spacing: 6) {
                    if d.approvable { TileButton(title: "Approve", primary: true) { model.approve(job) } }
                    TileButton(title: "See options") { model.onOpenJob(job.id) }
                }
                .padding(.top, 3)
            }
        case .needsYou(.approve):
            let approval = model.state.approval.flatMap { a in job.tasks.contains { $0.info.runID == a.runID } ? a : nil }
            Text(approval?.description ?? job.newest.info.shortStatus ?? "Waiting for your OK to continue")
                .font(.system(size: 11.5)).foregroundStyle(.secondary).lineLimit(3).fixedSize(horizontal: false, vertical: true)
            if approval != nil {
                HStack(spacing: 6) {
                    TileButton(title: "Approve once", primary: true) { model.onApproval("once") }
                    TileButton(title: "Deny") { model.onApproval("deny") }
                }
                .disabled(model.busy)
                .padding(.top, 4)
            }
        case .needsYou(.review):
            let drafts = job.tasks.flatMap(\.info.pendingDrafts).count
            detailsLine(drafts > 0 ? (drafts == 1 ? "An email draft to check" : "\(drafts) email drafts to check") : "Pictures to look at")
        case .failed:
            detailsLine(job.newest.info.failure?.label ?? job.newest.info.failure?.text ?? "Stopped with an error")
        case .running:
            let info = job.activeTask?.info ?? job.newest.info
            Text(taskStatusLine(info, now: model.state.now, assistantName: model.state.assistantName).0)
                .font(.system(size: 11.5)).foregroundStyle(.secondary).lineLimit(1)
            if let p = JobBoard.progress(info) { StepBar(done: p.done, total: p.total).padding(.top, 3) }
        case .done:
            detailsLine(job.newest.info.result?.label ?? "Done")
        }
    }

    private func detailsLine(_ text: String) -> some View {
        HStack(spacing: 6) {
            Text(text).font(.system(size: 11.5)).foregroundStyle(.secondary).lineLimit(2)
            Spacer(minLength: 4)
            Text("Details ›").font(.system(size: 11, weight: .semibold)).foregroundStyle(JobStyle.blueInk)
        }
    }
}

/// Plan steps as segments: done green, current blue, the rest grey.
struct StepBar: View {
    var done: Int
    var total: Int
    var body: some View {
        HStack(spacing: 3) {
            ForEach(0..<max(total, 1), id: \.self) { i in
                Capsule().fill(i < done ? JobStyle.green.opacity(0.85) : i == done ? Color(red: 0.37, green: 0.61, blue: 0.94) : Color.primary.opacity(0.12))
                    .frame(height: 4)
            }
        }
        .accessibilityLabel("Step \(min(done + 1, total)) of \(total)")
    }
}

// MARK: - Job view

struct JobDetailView: View {
    var job: Job
    @ObservedObject var model: VoicePanelModel

    var body: some View {
        let next = model.nextJobNeedingYou(after: job.id)
        VStack(alignment: .leading, spacing: 9) {
            HStack(spacing: 8) {
                AllJobsButton { model.onOpenJob(nil) }
                Spacer(minLength: 4)
                Text("esc").font(.system(size: 10, weight: .medium)).foregroundStyle(.tertiary)
                    .padding(.horizontal, 5).padding(.vertical, 1)
                    .overlay(RoundedRectangle(cornerRadius: 4).strokeBorder(Color.primary.opacity(0.15), lineWidth: 0.75))
                    .help("Esc goes back to all jobs")
                    .accessibilityHidden(true)
            }
            HStack(spacing: 5) {
                Image(systemName: "mic.fill").font(.system(size: 9, weight: .semibold))
                Text("Talking to: \(job.name)").font(.system(size: 11, weight: .semibold)).lineLimit(1)
            }
            .padding(.horizontal, 9).padding(.vertical, 3)
            .background(Capsule().fill(JobStyle.blue.opacity(0.35)))
            .foregroundStyle(JobStyle.blueInk)
            .help("What you say now goes to this job unless it's clearly about something else")
            if let answer = job.answerTask, let headline = JobAnswer.headline(answer.info) {
                // An answer: the chip above names what was asked about; the answer itself leads, big.
                Text(headline).font(.system(size: 16, weight: .semibold)).fixedSize(horizontal: false, vertical: true)
            } else {
                VStack(alignment: .leading, spacing: 2) {
                    Text(job.name).font(.system(size: 15, weight: .semibold)).fixedSize(horizontal: false, vertical: true)
                    let line = taskStatusLine(job.activeTask?.info ?? job.newest.info, now: model.state.now, assistantName: model.state.assistantName)
                    Text(job.stage.needsYou ? stageLine : line.0).font(.system(size: 11.5)).foregroundStyle(.secondary).lineLimit(2)
                }
            }
            PanelScrollView(maxHeight: min(JobStyle.scrollCap, 560 + model.extraHeight)) {
                VStack(alignment: .leading, spacing: 12) {
                    if let answer = job.answerTask { answerSections(answer) } else { sections }
                }
                    .padding(.trailing, 6)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            if let next {
                Button { model.onOpenJob(next.id) } label: {
                    HStack(spacing: 4) {
                        Spacer(minLength: 0)
                        Text(job.stage.needsYou ? "After you decide · Next:" : "Next:").foregroundStyle(.secondary)
                        Text("\(next.name) ›").fontWeight(.semibold)
                        Spacer(minLength: 0)
                    }
                    .font(.system(size: 11.5)).contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .help("Open the next job that needs you")
            }
        }
        .padding(.horizontal, 14).padding(.top, 10).padding(.bottom, 12)
    }

    /// An answer: its cards first and large, then the sorted report or the text, then pictures that
    /// came along (folded to a one-line row), then sources. Nothing to decide here.
    @ViewBuilder private func answerSections(_ task: TaskItem) -> some View {
        let cards = JobCards.items(job, model: model)
        let pictures = JobCards.pictures(job, model: model)
        ForEach(JobCards.answerBlocks(job, model: model), id: \.self) { block in
            switch block {
            case .cards: JobCardStack(job: job, items: cards, model: model)
            case .report: answerReport(task, hasCards: !cards.isEmpty).layoutProbe("answer.report")
            case .pictures:
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(pictures) { PictureRow(job: job, review: $0, model: model) }
                }
                .layoutProbe("answer.pictures")
            case .sources: answerSources(task)
            case .tasks: answerTasks
            }
        }
    }

    @ViewBuilder private func answerReport(_ task: TaskItem, hasCards: Bool) -> some View {
        if let shape = task.info.result?.shape {
            ReportBoard(shape: shape, full: task.info.result?.full ?? task.info.result?.spoken, showHeadline: false,
                        expanded: model.expandedReports.contains(task.id),
                        toggle: { if model.expandedReports.contains(task.id) { model.expandedReports.remove(task.id) } else { model.expandedReports.insert(task.id) } })
        } else if let text = task.info.result?.full ?? task.info.result?.spoken {
            VStack(alignment: .leading, spacing: 4) {
                SectionLabel(text: hasCards ? "In words" : "Answer")
                // Links read as their names here; the addresses are listed under Sources.
                Text(Self.readable(text)).font(.system(size: 12)).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    @ViewBuilder private func answerSources(_ task: TaskItem) -> some View {
        let links = task.info.cards
        VStack(alignment: .leading, spacing: 4) {
            SectionLabel(text: links.count == 1 ? "Source" : "Sources")
            ForEach(links) { link in
                Link(destination: link.url) {
                    HStack(spacing: 6) {
                        Image(systemName: "link").font(.system(size: 9.5, weight: .semibold)).foregroundStyle(.tertiary)
                        Text(link.title).font(.system(size: 11.5)).lineLimit(1)
                        Spacer(minLength: 4)
                        Text(link.url.host ?? "").font(.system(size: 10.5)).foregroundStyle(.tertiary).lineLimit(1)
                    }
                    .foregroundStyle(JobStyle.blueInk)
                }
                .help(link.url.absoluteString)
            }
        }
    }

    private var answerTasks: some View {
        VStack(alignment: .leading, spacing: 4) {
            SectionLabel(text: "In this job")
            ForEach(job.tasks.reversed()) { t in
                HStack(spacing: 6) {
                    Circle().fill(dot(t)).frame(width: 6, height: 6)
                    Text(t.name).font(.system(size: 11.5)).lineLimit(1)
                }
            }
        }
    }

    /// Markdown links `[name](url)` as just their names; bold markers dropped.
    static func readable(_ text: String) -> String {
        text.replacingOccurrences(of: #"\[([^\]\n]{1,100})\]\((https?://[^\s)]+)\)"#, with: "$1", options: .regularExpression)
            .replacingOccurrences(of: "**", with: "")
    }

    private var stageLine: String {
        switch job.stage {
        case .needsYou(.decide): return "Waiting for your decision"
        case .needsYou(.approve): return "Waiting for your OK"
        case .needsYou(.review): return "Waiting for you to look"
        default: return ""
        }
    }

    @ViewBuilder private var sections: some View {
        let decisionTask = job.decisionTask
        let answered = job.tasks.last { $0.info.decision != nil && model.answers[$0.id] != nil }
        if let task = decisionTask, let d = task.info.decision {
            DecisionBrief(job: job, task: task, decision: d, model: model)
        } else if let task = answered, let picked = model.answers[task.id] {
            HStack(alignment: .top, spacing: 7) {
                Image(systemName: "checkmark.circle.fill").foregroundStyle(JobStyle.green).font(.system(size: 12))
                VStack(alignment: .leading, spacing: 1) {
                    Text("You picked").font(.system(size: 10, weight: .semibold)).foregroundStyle(.tertiary)
                    Text(picked).font(.system(size: 12, weight: .medium)).fixedSize(horizontal: false, vertical: true)
                    if let q = task.info.decision?.question {
                        Text(q).font(.system(size: 10.5)).foregroundStyle(.tertiary).lineLimit(2)
                    }
                }
            }
        }
        if let approval = model.state.approval, job.tasks.contains(where: { $0.info.runID == approval.runID }) {
            ApprovalRow(approval: approval, busy: model.busy, onChoice: model.onApproval)
        }
        if let failed = job.tasks.last, failed.info.status == "failed" {
            VStack(alignment: .leading, spacing: 3) {
                SectionLabel(text: "What went wrong")
                Text(failed.info.failure?.text ?? "The task stopped with an error.").font(.system(size: 12))
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        // Every card of the job (a review, drafts, result cards): the most relevant open, the rest
        // one-line rows. Nothing else in the panel draws them.
        let cards = JobCards.items(job, model: model)
        if !cards.isEmpty { JobCardStack(job: job, items: cards, model: model) }
        LiveSteps(job: job, model: model)
        let reportTask = job.tasks.last { $0.info.result != nil && $0.id != decisionTask?.id }
        if let task = reportTask {
            if let shape = task.info.result?.shape {
                ReportBoard(shape: shape, full: task.info.result?.full ?? task.info.result?.spoken,
                            expanded: model.expandedReports.contains(task.id),
                            toggle: { if model.expandedReports.contains(task.id) { model.expandedReports.remove(task.id) } else { model.expandedReports.insert(task.id) } })
            } else if let text = task.info.result?.full ?? task.info.result?.spoken {
                VStack(alignment: .leading, spacing: 4) {
                    SectionLabel(text: "Result")
                    Text(text).font(.system(size: 12)).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        // Pictures that came along with a report: folded under it.
        ForEach(JobCards.pictures(job, model: model)) { PictureRow(job: job, review: $0, model: model) }
        if job.tasks.count > 1 {
            VStack(alignment: .leading, spacing: 4) {
                SectionLabel(text: "In this job")
                ForEach(job.tasks.reversed()) { task in
                    HStack(spacing: 6) {
                        Circle().fill(dot(task)).frame(width: 6, height: 6)
                        Text(task.name).font(.system(size: 11.5)).lineLimit(1)
                        Spacer(minLength: 4)
                        Text(task.isActive ? "running" : (task.info.status == "completed" ? "done" : task.info.status.replacingOccurrences(of: "_", with: " ")))
                            .font(.system(size: 10.5)).foregroundStyle(.tertiary)
                    }
                }
            }
        }
    }

    private func dot(_ t: TaskItem) -> Color {
        if t.isActive { return Color(red: 0.37, green: 0.61, blue: 0.94) }
        switch t.info.status { case "completed": return JobStyle.green; case "failed": return JobStyle.red; default: return .secondary }
    }
}

/// The decision a job waits on: the question in plain words, what it's about, a picture of the
/// problem (the task's own cards), why the recommendation, and the options.
struct DecisionBrief: View {
    var job: Job
    var task: TaskItem
    var decision: Decision
    @ObservedObject var model: VoicePanelModel

    var body: some View {
        let answer = model.answers[task.id]
        let visuals = task.info.views.filter { $0.kind != "question" }
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Circle().fill(answer == nil ? Tokens.amber : JobStyle.green).frame(width: 7, height: 7)
                Text(answer == nil ? "Needs you" : "Answered").font(.system(size: 10.5, weight: .semibold)).foregroundStyle(.secondary)
            }
            Text(decision.question).font(.system(size: 14, weight: .semibold)).fixedSize(horizontal: false, vertical: true)
            if let context = decision.context {
                Text(context).font(.system(size: 11.5)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            if !visuals.isEmpty {
                VStack(spacing: 8) { ForEach(visuals) { ViewCardView($0) } }
                    .environment(\.questionActions, model.questionActions(for: task.id))
            }
            if let affects = decision.affects {
                Label(affects, systemImage: "scope").font(.system(size: 11)).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            SectionLabel(text: "Options").padding(.top, 2)
            ForEach(Array(decision.options.enumerated()), id: \.offset) { i, option in
                DecisionOptionRow(number: i + 1, option: option, recommended: i == decision.recommended,
                                  why: i == decision.recommended ? decision.why : nil,
                                  chosen: answer == option.text, dimmed: answer != nil && answer != option.text,
                                  enabled: answer == nil) { model.pick(i + 1, in: job) }
            }
            if answer == nil {
                if decision.approvable, let rec = decision.recommendedOption {
                    TileButton(title: "Approve: \(rec.text)", primary: true) { model.approve(job) }
                }
                Text("Click, press 1–\(decision.options.count), or just say it.").font(.system(size: 10.5)).foregroundStyle(.tertiary)
            }
        }
        .padding(11)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Tokens.amber.opacity(answer == nil ? 0.08 : 0.0)))
        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous)
            .strokeBorder(answer == nil ? Tokens.amber.opacity(0.4) : Color.primary.opacity(0.08), lineWidth: 0.75))
    }
}

struct DecisionOptionRow: View {
    var number: Int
    var option: DecisionOption
    var recommended: Bool
    var why: String?
    var chosen: Bool
    var dimmed: Bool
    var enabled: Bool
    var action: () -> Void
    @State private var hover = false

    private var pills: [(String, JobStyle.Pill)] {
        var out = option.tags.map { ($0.text, JobStyle.pill(tone: $0.tone)) }
        let said = option.tags.map { $0.text.lowercased() }.joined(separator: " ")
        if let risk = option.risk, !said.contains("risk") {
            out.append(risk == "low" ? ("Low risk", .green) : risk == "medium" ? ("Some risk", .amber) : ("High risk", .red))
        }
        if let r = option.reversible, !said.contains("reversible"), !said.contains("undo") {
            out.append(r ? ("Reversible", .green) : ("Can't undo", .amber))
        }
        return out
    }

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text("\(number)").font(.system(size: 10.5, weight: .bold, design: .rounded))
                        .frame(width: 18, height: 18)
                        .background(RoundedRectangle(cornerRadius: 5).fill(chosen ? JobStyle.blue : Color.primary.opacity(0.09)))
                        .foregroundStyle(chosen ? Color.white : Color.secondary)
                    Text(option.text).font(.system(size: 12.5, weight: .semibold)).fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 4)
                    if recommended && !dimmed { JobPill(text: "Recommended", style: .blue) }
                    if chosen { Image(systemName: "checkmark").font(.system(size: 11, weight: .bold)).foregroundStyle(JobStyle.blueInk) }
                }
                if let detail = option.detail {
                    Text(detail).font(.system(size: 11.5)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        .padding(.leading, 26)
                }
                if let why {
                    Text("Why: \(why)").font(.system(size: 11)).foregroundStyle(.secondary).italic()
                        .fixedSize(horizontal: false, vertical: true).padding(.leading, 26)
                }
                if !pills.isEmpty {
                    WrapLayout(spacing: 4) { ForEach(Array(pills.enumerated()), id: \.offset) { JobPill(text: $0.element.0, style: $0.element.1) } }
                        .padding(.leading, 26)
                }
            }
            .padding(.horizontal, 10).padding(.vertical, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(recommended && !dimmed ? JobStyle.blue.opacity(hover && enabled ? 0.22 : 0.14) : Color.primary.opacity(hover && enabled ? 0.09 : 0.05)))
            .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous)
                .strokeBorder(chosen || (recommended && !dimmed) ? JobStyle.blueInk.opacity(0.6) : Color.primary.opacity(0.08), lineWidth: 1))
            .opacity(dimmed ? 0.45 : 1)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .onHover { hover = $0 }
        .accessibilityLabel("Option \(number): \(option.text)\(recommended ? ", recommended" : "")")
    }
}

/// What the job is doing now, step by step, with a way to add something or stop it.
struct LiveSteps: View {
    var job: Job
    @ObservedObject var model: VoicePanelModel

    var body: some View {
        if let task = job.activeTask ?? recentPlanned {
            let info = task.info
            VStack(alignment: .leading, spacing: 6) {
                SectionLabel(text: task.isActive ? "Now" : "Steps")
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(rows(info).enumerated()), id: \.offset) { i, row in
                        StepRow(text: row.text, sub: row.sub, state: row.state, last: i == rows(info).count - 1)
                    }
                }
                if task.isActive && model.state.connection.isOpen {
                    HStack(spacing: 6) {
                        TileButton(title: "Tell it something") { model.onTellJob(job.id) }
                        if task.canStop, let run = info.runID {
                            Button { model.onStopTask(run) } label: {
                                Text("Stop").font(.system(size: 11.5, weight: .semibold)).frame(maxWidth: .infinity).frame(height: 26)
                                    .foregroundStyle(Color(red: 1, green: 0.7, blue: 0.66))
                                    .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(Tokens.red.opacity(0.28)))
                                    .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            .help("Stop this task. The call and other jobs continue.")
                        }
                    }
                    if model.tellingJobID == job.id {
                        Text("Go ahead: what you say now goes to \(job.name).").font(.system(size: 11)).foregroundStyle(JobStyle.blueInk)
                    }
                }
            }
        }
    }

    /// A finished task that had a plan still shows its steps, ticked.
    private var recentPlanned: TaskItem? { job.tasks.last.flatMap { $0.info.plan.isEmpty ? nil : $0 } }

    struct Row { var text: String; var sub: String?; var state: StepRow.State }

    private func rows(_ info: WorkInfo) -> [Row] {
        let milestones = info.milestones
        if let p = JobBoard.progress(info) {
            return info.plan.enumerated().map { i, step in
                let sub = i < milestones.count && milestones[i].text != step ? milestones[i].text : nil
                if i < p.done { return Row(text: step, sub: sub, state: .done) }
                if i == p.done && !info.isTerminal { return Row(text: step, sub: info.shortStatus, state: .current) }
                return Row(text: step, sub: nil, state: info.isTerminal && info.status == "completed" ? .done : .next)
            }
        }
        var out = milestones.suffix(5).map { Row(text: $0.text, sub: $0.at.map(WorkDetailView.time), state: .done) }
        if !info.isTerminal, let now = info.shortStatus { out.append(Row(text: now, sub: info.detail, state: .current)) }
        return out
    }
}

struct StepRow: View {
    enum State { case done, current, next }
    var text: String
    var sub: String?
    var state: State
    var last: Bool
    var body: some View {
        HStack(alignment: .top, spacing: 9) {
            VStack(spacing: 0) {
                Circle()
                    .fill(state == .done ? JobStyle.green : state == .current ? Color(red: 0.37, green: 0.61, blue: 0.94) : Color.primary.opacity(0.2))
                    .frame(width: 9, height: 9)
                    .overlay(Circle().strokeBorder(Color.white.opacity(state == .current ? 0.6 : 0), lineWidth: 1.5))
                    .padding(.top, 3)
                if !last { Rectangle().fill(Color.primary.opacity(0.14)).frame(width: 1.5).frame(maxHeight: .infinity) }
            }
            .frame(width: 10)
            VStack(alignment: .leading, spacing: 1) {
                Text(text).font(.system(size: 12, weight: state == .next ? .regular : .semibold))
                    .foregroundStyle(state == .next ? Color.secondary : Color.primary)
                    .fixedSize(horizontal: false, vertical: true)
                if let sub { Text(sub).font(.system(size: 10.5)).foregroundStyle(.secondary).lineLimit(2) }
            }
            .padding(.bottom, last ? 0 : 8)
        }
        // Rows take their natural height, so the connector line fills it instead of growing forever.
        .fixedSize(horizontal: false, vertical: true)
    }
}

/// A sorted report: checks as tiles, what shipped, what's still open, then the sections.
/// The full text is one click away.
struct ReportBoard: View {
    var shape: ResultShape
    var full: String?
    /// False when the view above already leads with the headline (an answer).
    var showHeadline = true
    var expanded: Bool
    var toggle: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if showHeadline && !shape.headline.isEmpty {
                Text(shape.headline).font(.system(size: 13, weight: .semibold)).fixedSize(horizontal: false, vertical: true)
            }
            if !shape.checks.isEmpty {
                SectionLabel(text: "Checks")
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 92), spacing: 6)], spacing: 6) {
                    ForEach(Array(shape.checks.prefix(6).enumerated()), id: \.offset) { _, c in
                        VStack(alignment: .leading, spacing: 1) {
                            Text(c.value).font(.system(size: 15, weight: .bold)).lineLimit(1).minimumScaleFactor(0.6)
                                .foregroundStyle(c.good ? JobStyle.green : JobStyle.amber)
                            Text(c.label).font(.system(size: 10)).lineLimit(2)
                                .foregroundStyle(c.good ? JobStyle.green.opacity(0.75) : JobStyle.amber.opacity(0.8))
                        }
                        .padding(.horizontal, 8).padding(.vertical, 6)
                        .frame(maxWidth: .infinity, minHeight: 44, alignment: .topLeading)
                        .background(RoundedRectangle(cornerRadius: 9, style: .continuous)
                            .fill((c.good ? JobStyle.green : JobStyle.amber).opacity(0.13)))
                    }
                }
            }
            list("Shipped", shape.shipped, JobStyle.green)
            list("Still open", shape.open, JobStyle.amber)
            ForEach(Array(shape.sections.prefix(5).enumerated()), id: \.offset) { _, s in
                list(s.title, s.points, Color.secondary)
            }
            if shape.undo {
                Label("This can be undone: say \u{201C}undo it\u{201D}.", systemImage: "arrow.uturn.backward")
                    .font(.system(size: 11)).foregroundStyle(.secondary)
            }
            if let full {
                Button(action: toggle) {
                    Text(expanded ? "Hide full report" : "Full report ›").font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(JobStyle.blueInk).contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                if expanded {
                    Text(full).font(.system(size: 11.5)).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                        .padding(9).frame(maxWidth: .infinity, alignment: .leading)
                        .background(RoundedRectangle(cornerRadius: 9, style: .continuous).fill(Color.primary.opacity(0.06)))
                }
            }
        }
    }

    @ViewBuilder private func list(_ title: String, _ items: [String], _ color: Color) -> some View {
        if !items.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                SectionLabel(text: title)
                ForEach(Array(items.prefix(8).enumerated()), id: \.offset) { _, item in
                    HStack(alignment: .firstTextBaseline, spacing: 7) {
                        Circle().fill(color).frame(width: 6, height: 6).alignmentGuide(.firstTextBaseline) { $0[.bottom] - 1 }
                        Text(item).font(.system(size: 12)).fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }
}

// MARK: - Cards in a job view

/// "‹ All jobs": the way back to the board, top-left of every job and answer view.
struct AllJobsButton: View {
    var action: () -> Void
    @State private var hover = false
    var body: some View {
        Button(action: action) {
            HStack(spacing: 4) {
                Image(systemName: "chevron.left").font(.system(size: 10, weight: .bold))
                Text("All jobs").font(.system(size: 12, weight: .semibold))
            }
            .foregroundStyle(JobStyle.blueInk)
            .padding(.leading, 7).padding(.trailing, 10).frame(height: 26)
            .background(Capsule().fill(JobStyle.blueInk.opacity(hover ? 0.2 : 0.12)))
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
        .help("Back to all your jobs (Esc)")
        .accessibilityLabel("All jobs")
    }
}

/// One card of a job: a review, an email draft, or a result card.
struct JobCardItem: Identifiable {
    enum Content { case review(ImageReview), draft(EmailDraft), view(taskID: String, card: ViewCard) }
    var key: String
    var icon: String
    var title: String
    var summary: String
    var content: Content
    var id: String { key }
}

enum JobCards {
    /// A job's cards, most relevant first: what waits on you (a review, then drafts), then the
    /// newest result's cards, then older ones. The decision's own pictures stay in its brief.
    @MainActor static func items(_ job: Job, model: VoicePanelModel) -> [JobCardItem] {
        var out: [JobCardItem] = []
        let ids = Set(job.tasks.map(\.id))
        for r in model.pinnedReviews.reversed() where ids.contains(r.taskID) && !isIncidental(r, in: job) {
            out.append(JobCardItem(key: "review:\(r.runID)", icon: "photo.on.rectangle.angled", title: "Ready to review",
                                   summary: r.countLabel, content: .review(r)))
        }
        var seen = Set<String>()
        for d in job.tasks.reversed().flatMap(\.info.emailDrafts) where seen.insert(d.draftID).inserted {
            let to = d.to.first.map { "To \($0)" } ?? ""
            out.append(JobCardItem(key: "draft:\(d.draftID)", icon: "envelope", title: d.subject.isEmpty ? "Email draft" : d.subject,
                                   summary: to, content: .draft(d)))
        }
        for t in job.tasks.reversed() where t.id != job.decisionTaskID {
            for v in t.info.views where v.kind != "question" {
                let (icon, title, summary) = describe(v)
                out.append(JobCardItem(key: "view:\(t.id):\(v.number)", icon: icon, title: title, summary: summary,
                                       content: .view(taskID: t.id, card: v)))
            }
        }
        return out
    }

    /// Pictures that came with a finished report (`WorkInfo.reviewIsIncidental`): they fold to a
    /// one-line row under the report instead of leading the job as something to review.
    @MainActor static func pictures(_ job: Job, model: VoicePanelModel) -> [ImageReview] {
        model.pinnedReviews.reversed().filter { isIncidental($0, in: job) }
    }

    static func isIncidental(_ r: ImageReview, in job: Job) -> Bool {
        job.tasks.first { $0.id == r.taskID }?.info.reviewIsIncidental ?? false
    }

    /// What an answer view draws, top to bottom: its cards, the report, pictures that came along,
    /// sources, the job's other tasks. The report always comes before the pictures.
    enum AnswerBlock: Hashable { case cards, report, pictures, sources, tasks }

    @MainActor static func answerBlocks(_ job: Job, model: VoicePanelModel) -> [AnswerBlock] {
        guard let task = job.answerTask else { return [] }
        var out: [AnswerBlock] = []
        if !items(job, model: model).isEmpty { out.append(.cards) }
        if task.info.result?.shape != nil || (task.info.result?.full ?? task.info.result?.spoken) != nil { out.append(.report) }
        if !pictures(job, model: model).isEmpty { out.append(.pictures) }
        if !task.info.cards.isEmpty { out.append(.sources) }
        if job.tasks.count > 1 { out.append(.tasks) }
        return out
    }

    /// Icon, title and a few words for a card's one-line row.
    static func describe(_ c: ViewCard) -> (String, String, String) {
        let title = c["title"].string ?? c["place"].string ?? c["name"].string
        func label(_ fallback: String) -> String { title ?? fallback }
        switch c.kind {
        case "weather":
            let parts = [c["temp"].double.map { ViewCard.degrees($0) }, c["condition"].string?.capitalized]
            return ("cloud.sun", "Weather" + (c["place"].string.map { " · \($0)" } ?? ""), parts.compactMap { $0 }.joined(separator: " · "))
        case "quote":
            let price = c["price"].double.map { ViewCard.money($0, currency: c["currency"].string) }
            return ("chart.line.uptrend.xyaxis", c["name"].string ?? c["symbol"].string ?? "Price", price ?? "")
        case "chart": return ("chart.bar", label("Chart"), c["labels"].strings.count > 1 ? "\(c["labels"].strings.first!)–\(c["labels"].strings.last!)" : "")
        case "stats", "nutrition":
            let items = c["items"].array.prefix(2).compactMap { i in i["value"].string.map { "\($0) \((i["label"].string ?? "").lowercased())" } }
            return ("square.grid.2x2", label("Numbers"), items.joined(separator: " · "))
        case "comparison": return ("rectangle.split.2x1", label("Comparison"), c["winner"].string.map { "\($0) comes out ahead" } ?? "")
        case "list", "steps": return ("list.bullet", label("List"), "\(c["items"].array.count) items")
        case "place", "places", "route", "transit": return ("mappin.and.ellipse", label("Places"), c["items"].array.isEmpty ? "" : "\(c["items"].array.count) places")
        case "news": return ("newspaper", label("News"), c["items"].array.isEmpty ? "" : "\(c["items"].array.count) stories")
        case "agenda", "calendar_day":
            let n = c["days"].array.reduce(0) { $0 + $1["events"].array.count } + c["events"].array.count
            return ("calendar", label("Calendar"), n == 0 ? "" : "\(n) events")
        case "flight": return ("airplane", label("Flight"), c["status"].string ?? "")
        case "devices": return ("laptopcomputer.and.iphone", label("Devices"), "\(c["items"].array.count) devices")
        case "lanes": return ("chart.bar.xaxis", label("Timeline"), c["note"].string ?? "")
        case "flow": return ("arrow.triangle.branch", label("Flow"), "")
        case "before_after": return ("arrow.left.arrow.right", label("Before and after"), c["after"]["text"].string ?? "")
        case "fact", "conversion", "math": return ("number", label("Answer"), c["value"].string ?? c["to_value"].string ?? c["result"].string ?? "")
        default: return ("square.text.square", label(c.summary), "")
        }
    }
}

/// A job's cards: one card shows whole, the others fold to a one-line row (icon, title, a few
/// words, chevron). Clicking a row opens it; clicking an open card's row folds it. A single card
/// shows whole with no row.
struct JobCardStack: View {
    var job: Job
    var items: [JobCardItem]
    @ObservedObject var model: VoicePanelModel

    /// How tall a picture in a job view gets; click it for full size.
    static let imageCap: CGFloat = 170

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if items.count == 1, let only = items.first {
                content(only)
            } else {
                ForEach(Array(items.enumerated()), id: \.element.id) { i, item in
                    let open = model.isCardOpen(job: job.id, key: item.key, index: i)
                    VStack(alignment: .leading, spacing: 6) {
                        JobCardRow(item: item, open: open) { model.toggleCard(job: job.id, key: item.key, index: i) }
                        if open { content(item).padding(.bottom, 4) }
                    }
                }
            }
        }
    }

    @ViewBuilder private func content(_ item: JobCardItem) -> some View {
        switch item.content {
        case .review(let review):
            ImageReviewCard(review: review, shownCount: 1, model: model, inJob: true, previewCap: Self.imageCap)
        case .draft(let draft):
            EmailDraftCard(draft: draft, model: model)
        case .view(let taskID, let card):
            ViewCardView(card).environment(\.questionActions, model.questionActions(for: taskID))
        }
    }
}

struct JobCardRow: View {
    var item: JobCardItem
    var open: Bool
    var action: () -> Void
    @State private var hover = false
    var body: some View {
        Button(action: action) {
            HStack(spacing: 8) {
                Image(systemName: item.icon).font(.system(size: 11, weight: .semibold)).foregroundStyle(JobStyle.blueInk)
                    .frame(width: 16)
                Text(item.title).font(.system(size: 12, weight: .semibold)).lineLimit(1).layoutPriority(1)
                if !item.summary.isEmpty {
                    Text(item.summary).font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(1).truncationMode(.tail)
                }
                Spacer(minLength: 4)
                Image(systemName: open ? "chevron.down" : "chevron.right").font(.system(size: 9, weight: .bold))
                    .foregroundStyle(.tertiary)
            }
            .padding(.horizontal, 10).frame(height: 30)
            .background(RoundedRectangle(cornerRadius: 9, style: .continuous).fill(Color.primary.opacity(hover ? 0.10 : 0.055)))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
        .help(open ? "Fold this card" : "Open this card")
        .accessibilityLabel("\(item.title)\(item.summary.isEmpty ? "" : ", \(item.summary)"), \(open ? "open" : "folded")")
    }
}

/// Pictures that came along with an answer: one folded row ("1 picture ›") under the report; click
/// to show the capped picture (click that for full size).
struct PictureRow: View {
    var job: Job
    var review: ImageReview
    @ObservedObject var model: VoicePanelModel

    static func label(_ r: ImageReview) -> String { r.images.count == 1 ? "1 picture" : "\(r.images.count) pictures" }

    var body: some View {
        let open = model.isPicturesOpen(job: job.id, run: review.runID)
        VStack(alignment: .leading, spacing: 6) {
            JobCardRow(item: JobCardItem(key: "pictures:\(review.runID)", icon: "photo", title: Self.label(review),
                                         summary: review.images.first?.name ?? "", content: .review(review)),
                       open: open) { model.togglePictures(job: job.id, run: review.runID) }
            if open {
                ImageReviewCard(review: review, shownCount: 1, model: model, inJob: true, previewCap: JobCardStack.imageCap,
                                heading: "Came with the answer")
            }
        }
    }
}

/// Where a few job-view blocks were last drawn (window coordinates, y down), so the panel smoke can
/// check the real on-screen order. Written on layout; cheap.
@MainActor enum LayoutProbe {
    static var frames: [String: CGRect] = [:]
}

extension View {
    func layoutProbe(_ key: String) -> some View {
        background(GeometryReader { g in
            let frame = g.frame(in: .global)
            Color.clear
                .onAppear { LayoutProbe.frames[key] = frame }
                .onChange(of: frame) { _, new in LayoutProbe.frames[key] = new }
                .onDisappear { LayoutProbe.frames[key] = nil }
        })
    }
}
