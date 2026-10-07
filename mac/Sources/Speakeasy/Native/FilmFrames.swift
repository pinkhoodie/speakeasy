import AppKit
import SwiftUI
import SpeakeasyCore
import SpeakeasyClient

/// `--film-frames <dir>`: draws the real call panel through a scripted call (invented data) and saves
/// one transparent 2x PNG per beat, for the launch video. Offline: no api, mic or network.
@MainActor
enum FilmFrames {
    private static var retained: NativeVoiceClient?

    static func run(config: AppConfig, dir: String) {
        let client = NativeVoiceClient(config: config)
        retained = client
        client.model.loadCardImage = { _, n in FileManager.default.contents(atPath: "\(dir)/picture-\(n).png") }
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        FileHandle.standardError.write("film: start\n".data(using: .utf8)!)
        Task { @MainActor in
            FileHandle.standardError.write("film: task\n".data(using: .utf8)!)
            for (name, state) in beats() {
                                client.showPreview(state, workExpanded: false)
                try? await Task.sleep(nanoseconds: 900_000_000)
                do { try snapshot(client, to: "\(dir)/\(name).png"); print("film \(name)") }
                catch { print("film FAILED \(name): \(error)") }
            }
            exit(0)
        }
    }

    static func snapshot(_ c: NativeVoiceClient, to path: String) throws {
        let panel = c.panel
        guard let view = panel.window.contentView else { throw CocoaError(.fileWriteUnknown) }
        panel.setNeedsResize()
        view.layoutSubtreeIfNeeded()
        let bounds = view.bounds
        guard let rep = view.bitmapImageRepForCachingDisplay(in: bounds) else { throw CocoaError(.fileWriteUnknown) }
        view.cacheDisplay(in: bounds, to: rep)
        let scale: CGFloat = 2
        guard let out = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(bounds.width * scale),
                                         pixelsHigh: Int(bounds.height * scale), bitsPerSample: 8, samplesPerPixel: 4,
                                         hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                                         bytesPerRow: 0, bitsPerPixel: 0) else { throw CocoaError(.fileWriteUnknown) }
        out.size = bounds.size
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: out)
        NSGraphicsContext.current?.imageInterpolation = .high
        let shape = NSBezierPath(roundedRect: bounds, xRadius: Tokens.radius, yRadius: Tokens.radius)
        NSColor(calibratedRed: 0.11, green: 0.12, blue: 0.15, alpha: 0.94).setFill()
        shape.fill()
        shape.addClip()
        rep.draw(in: bounds)
        NSGraphicsContext.restoreGraphicsState()
        guard let png = out.representation(using: .png, properties: [:]) else { throw CocoaError(.fileWriteUnknown) }
        try png.write(to: URL(fileURLWithPath: path))
    }

    // MARK: Script

    static func card(_ json: [String: Any]) -> ViewCard { ViewCard(json: json, number: 1)! }

    static func task(_ id: String, _ status: String, _ request: String, title: String, short: String?,
                     age: TimeInterval, now: Date, result: WorkResult? = nil,
                     milestones: [String] = []) -> TaskItem {
        var events = [WorkEventItem(kind: "request", text: request, at: now - age - 2)]
        for (i, m) in milestones.enumerated() {
            events.append(WorkEventItem(kind: "milestone", text: m, at: now - age + Double(i)))
        }
        return TaskItem(id: id, info: WorkInfo(runID: "run_\(id)", status: status, updated: now - age, shortStatus: short,
                                               updatedAt: now - 1, statusSource: "authored", events: events,
                                               result: result, title: title))
    }

    static func beats() -> [(String, VoiceState)] {
        let now = Date()
        func base() -> VoiceState {
            var s = VoiceState(); s.now = now; s.connection = .live; s.interactionID = "vi_film"; return s
        }
        let quote = card(["kind": "quote", "asset": "stock", "symbol": "NVDA", "name": "NVIDIA", "price": 187.42,
                          "currency": "USD", "change": 3.86, "change_pct": 2.1,
                          "points": [183.56, 184.1, 183.2, 184.9, 185.6, 185.1, 186.0, 186.9, 186.4, 187.1, 186.8, 187.42],
                          "previous_close": 183.56, "range_label": "Today", "exchange": "NASDAQ"])
        let quoteTask = task("q", "completed", "What's Nvidia at?", title: "NVIDIA price", short: "Done", age: 1, now: now,
                             result: WorkResult(spoken: "Nvidia's at 187.42, up 2.1% today.", full: nil, views: [quote]))
        let home = card(["kind": "home", "title": "Living room", "devices": [
            ["name": "Ceiling lights", "kind": "light", "on": true, "level": 30],
            ["name": "Lamp", "kind": "light", "on": true, "level": 30],
            ["name": "Speaker", "kind": "media", "on": true, "state": "Playing jazz"]]])
        let homeTask = task("h", "completed", "Dim the living room and put on some jazz", title: "Living room", short: "Done",
                            age: 1, now: now, result: WorkResult(spoken: "Done.", full: nil, views: [home]))
        let dinnerReq = "Find us a dinner spot near the office Thursday, for four, and draft an invite to the team"
        let dinnerWorking = task("d", "working", dinnerReq, title: "Thursday dinner for four",
                                 short: "Checking tables for 7 PM", age: 6, now: now,
                                 milestones: ["Pulled places within a 10-minute walk", "Checking tables for 7 PM"])
        let game = card(["kind": "game", "league": "NBA", "status": "final", "detail": "Final", "when": "Last night",
                         "teams": [["name": "Knicks", "abbr": "NYK", "score": "112", "winner": true, "record": "3-1"],
                                   ["name": "Celtics", "abbr": "BOS", "score": "104", "winner": false, "record": "2-2"]]])
        let gameTask = task("g", "completed", "Who won the Knicks game?", title: "Knicks score", short: "Done", age: 1,
                            now: now, result: WorkResult(spoken: "The Knicks beat Boston 112 to 104.", full: nil, views: [game]))
        let question = card(["kind": "question", "question": "Three places have a table at 7. Which one?",
                             "options": ["Hearth & Vine", "Osteria Nove", "Baan Lime"], "recommended": 1, "images": [1, 2, 3]])
        let dinnerAsk = task("d", "working", dinnerReq, title: "Thursday dinner for four", short: "Needs your pick", age: 2,
                             now: now, result: WorkResult(spoken: nil, full: nil, views: [question]))
        let draft = EmailDraft(draftID: "film_draft", sha256: "f11m", from: "you@example.com",
                               to: ["team@example.com"], cc: [], subject: "Dinner Thursday, 7 PM",
                               body: "Hi all,\n\nTable for four at Osteria Nove this Thursday at 7. It's a five-minute walk from the office.\n\nSee you there!")
        var dinnerDraft = task("d", "waiting_for_approval", dinnerReq, title: "Thursday dinner for four",
                               short: "Booked 7 PM · invite drafted", age: 2, now: now)
        dinnerDraft.info.emailDrafts = [draft]

        var out: [(String, VoiceState)] = []
        var s = base()
        s.exchange = Exchange(you: "", assistant: "", replyStarted: false)
        out.append(("01-idle", s))
        s = base(); s.exchange = Exchange(you: "What's Nvidia at?", assistant: "", replyStarted: false)
        out.append(("02-ask-quote", s))
        s = base(); s.speech = .speaking(lastDelta: now)
        s.exchange = Exchange(you: "What's Nvidia at?", assistant: "Nvidia's at 187.42, up 2.1% today.", replyStarted: true)
        s.tasks = [quoteTask]; s.workInfo = quoteTask.info
        out.append(("03-quote", s))
        s = base(); s.speech = .speaking(lastDelta: now)
        s.exchange = Exchange(you: "Dim the living room and put on some jazz.", assistant: "Done.", replyStarted: true)
        s.tasks = [quoteTask, homeTask]; s.workInfo = homeTask.info
        out.append(("04-home", s))
        s = base(); s.speech = .speaking(lastDelta: now)
        s.exchange = Exchange(you: "Find us a dinner spot near the office Thursday, for four, and draft an invite to the team.",
                              assistant: "On it. I'll let you know what I find.", replyStarted: true)
        s.tasks = [homeTask, dinnerWorking]; s.runID = "run_d"; s.delegationAt = now - 6; s.workInfo = dinnerWorking.info
        out.append(("05-dinner-working", s))
        s = base(); s.speech = .speaking(lastDelta: now)
        s.exchange = Exchange(you: "Who won the Knicks game?", assistant: "The Knicks beat Boston 112 to 104.", replyStarted: true)
        s.tasks = [dinnerWorking, gameTask]; s.runID = "run_d"; s.delegationAt = now - 20; s.workInfo = dinnerWorking.info
        out.append(("06-game", s))
        s = base(); s.speech = .speaking(lastDelta: now)
        s.exchange = Exchange(you: "", assistant: "Three spots at 7. I'd go with Osteria Nove.", replyStarted: true)
        s.tasks = [gameTask, dinnerAsk]; s.runID = "run_d"; s.workInfo = dinnerAsk.info
        out.append(("07-question", s))
        s = base(); s.speech = .speaking(lastDelta: now)
        s.exchange = Exchange(you: "Osteria Nove.", assistant: "Booked for 7. The invite's on screen. Tap Send when it looks right.",
                              replyStarted: true)
        var gameQuiet = gameTask; gameQuiet.info.result = WorkResult(spoken: "The Knicks beat Boston 112 to 104.", full: nil)
        s.tasks = [gameQuiet, dinnerDraft]; s.runID = "run_d"; s.workInfo = dinnerDraft.info
        out.append(("08-draft", s))
        return out.map { name, state in var st = state; st.work = deriveWork(st, now: now); return (name, st) }
    }
}
