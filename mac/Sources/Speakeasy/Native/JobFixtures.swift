import Foundation
import SpeakeasyCore
import SpeakeasyClient

/// Job-view fixtures: a board with several jobs cooking, and three jobs at the decision stage and
/// after the pick. Generic data only (this repo is public).
extension PreviewFixtures {
    static let jobNames = ["jobs-board", "job-release", "job-release-after", "job-nightly", "job-nightly-after",
                           "job-rollout", "job-rollout-after",
                           "orb-idle", "orb-talking", "orb-working", "orb-needs", "orb-failed"]

    /// Task ids whose question the fixtures answer in the "-after" states.
    static let jobQuestionTasks = ["rel_q", "night_q", "roll_q", "router_q"]

    /// How a fixture is shown: which job opens, what's already answered, folded into the orb or not,
    /// and the mic level (the user talking).
    struct JobSetup {
        var open: String?
        var answers: [String: String] = [:]
        var orb = false
        var level: Double = 0
    }

    static func jobSetup(_ name: String) -> JobSetup? {
        switch name {
        case "jobs-board": return JobSetup()
        case "job-release": return JobSetup(open: "job:rel")
        case "job-release-after": return JobSetup(open: "job:rel", answers: ["rel_q": "Ship the plugin now, the app after you try it"])
        case "job-nightly": return JobSetup(open: "job:night")
        case "job-nightly-after": return JobSetup(open: "job:night", answers: ["night_q": "Keep both fixes"])
        case "job-rollout": return JobSetup(open: "job:roll")
        case "job-rollout-after": return JobSetup(open: "job:roll", answers: ["roll_q": "Update the last two now"])
        case "orb-idle", "orb-working", "orb-needs", "orb-failed": return JobSetup(orb: true)
        case "orb-talking": return JobSetup(orb: true, level: 0.6)
        default: return nil
        }
    }

    /// Apply a job fixture to a client: show the state, then open the job or fold into the orb.
    @MainActor
    static func showJobFixture(_ name: String, on c: NativeVoiceClient) -> Bool {
        guard let setup = jobSetup(name), let (state, _) = jobState(name) else { return false }
        for id in jobQuestionTasks { c.model.answers[id] = nil; c.model.dismissedViewTasks.remove(id) }
        for (id, text) in setup.answers { c.model.answers[id] = text }
        c.model.expandedReports = []
        c.model.slim = setup.orb
        c.showPreview(state, workExpanded: false)
        c.model.orbLevel = setup.level
        if let open = setup.open { c.model.onOpenJob(open) } else { c.model.onOpenJob(nil) }
        c.model.boardFocusID = nil
        return true
    }

    static func jobState(_ name: String) -> (VoiceState, workExpanded: Bool)? {
        guard let setup = jobSetup(name) else { return nil }
        let now = Date()
        var s = VoiceState()
        s.now = now
        s.connection = .live
        s.interactionID = "vi_preview"
        s.exchange = Exchange(you: "What needs me?", assistant: "Three things need you; two are still running.", replyStarted: true)

        func task(_ id: String, job: (String, String)?, title: String, status: String = "completed", age: TimeInterval,
                  short: String? = nil, detail: String? = nil, plan: [String] = [], milestones: [String] = [],
                  result: [String: Any]? = nil, failure: String? = nil) -> TaskItem {
            var json: [String: Any] = ["task_id": id, "run_id": "run_\(id)", "status": status, "title": title,
                                       "updated_at": now.timeIntervalSince1970 - age, "status_source": "authored"]
            if let job { json["job"] = ["id": job.0, "name": job.1] }
            if let short { json["short_status"] = short }
            if let detail { json["detail"] = detail }
            if !plan.isEmpty { json["plan"] = plan }
            json["events"] = [["kind": "request", "text": title, "at": now.timeIntervalSince1970 - age - 120]]
                + milestones.enumerated().map { i, m in
                    ["kind": "milestone", "text": m, "at": now.timeIntervalSince1970 - age - Double(90 - i * 20)] as [String: Any] }
            if let result { json["result"] = result }
            if let failure { json["failure"] = ["kind": "error", "text": failure] }
            return TaskItem(json: json)!
        }

        let rel = ("rel", "Release v2.4")
        let night = ("night", "Nightly jobs")
        let roll = ("roll", "App rollout")
        let router = ("router", "Router setting")

        // Release: tests pass, the plugin and the app can ship together or one after the other.
        let relQuestion: [String: Any] = [
            "kind": "question", "question": "Ship the plugin now, or wait and ship both together?",
            "context": "Tests and the offline checks passed. The Mac app has a new board you haven't tried yet.",
            "why": "The plugin change is small and can be rolled back; the app is the part worth a look first.",
            "options": [
                ["text": "Ship the plugin now, the app after you try it", "detail": "Plugin goes out today; the app waits for your OK.",
                 "tags": [["text": "Safest", "tone": "good"]], "risk": "low", "reversible": true],
                ["text": "Ship both now", "detail": "Everyone gets the new board before you've used it.", "risk": "medium", "reversible": true],
                ["text": "Hold both", "detail": "Nothing changes until you say so."]],
            "recommended": 0, "affects": "2 downloads · the public site", "approvable": true]
        let relFlow: [String: Any] = ["kind": "flow", "title": "Where it goes", "nodes": [
            ["label": "Code", "detail": "main", "tone": "good"], ["label": "Checks", "detail": "all passed", "tone": "good"],
            ["label": "Plugin", "detail": "v2.4", "tone": "info"], ["label": "Mac app", "detail": "waits", "tone": "warn"]]]
        var relTasks = [
            task("rel_build", job: rel, title: "Build and test v2.4", age: 1800,
                 result: ["spoken": "Built and tested.", "label": "Built and tested"]),
            task("rel_q", job: rel, title: "Prepare the release", age: 240, plan: ["Build", "Run tests", "Offline checks", "Write notes"],
                 milestones: ["Built", "Tests passed", "Offline checks passed", "Release notes written"],
                 result: ["spoken": "Ready to ship. Plugin now and the app after you try it?", "label": "Ready to ship",
                          "views": [relQuestion, relFlow]]),
        ]
        if setup.answers["rel_q"] != nil {
            relTasks.append(task("rel_ship", job: rel, title: "Ship the plugin", age: 30, result: [
                "spoken": "The plugin is out. The app waits for you.",
                "full": "Shipped plugin v2.4. Tests: 248 passed. Offline checks: ok. The Mac app build is ready for you to try; say ship the app when you're happy.",
                "label": "Plugin shipped",
                "shape": ["type": "ship", "headline": "Plugin v2.4 is out; the app waits for you",
                          "checks": [["label": "tests passed", "value": "248", "good": true], ["label": "offline checks", "value": "OK", "good": true],
                                     ["label": "app build", "value": "Ready", "good": true]],
                          "shipped": ["Plugin v2.4 on the download page", "Release notes"],
                          "open": ["Try the new board, then say \u{201C}ship the app\u{201D}"], "undo": true]]))
        }

        // Nightly: two jobs failed overnight; a fix is ready.
        let nightQuestion: [String: Any] = [
            "kind": "question", "question": "Keep the two overnight fixes?",
            "context": "The backup and the report job both ran into the same lock at 2 am. Each now waits for the other.",
            "why": "Both fixes only change the start time, and last night's rerun passed with them.",
            "options": [
                ["text": "Keep both fixes", "detail": "Backup moves to 1:30, the report to 3:00.",
                 "tags": [["text": "Tested last night", "tone": "good"]], "risk": "low", "reversible": true],
                ["text": "Keep the backup fix only", "detail": "The report may still clash.", "risk": "medium", "reversible": true],
                ["text": "Undo both"]],
            "recommended": 0, "affects": "2 scheduled jobs", "approvable": true]
        let lanes: [String: Any] = ["kind": "lanes", "title": "Last night", "ticks": ["12a", "1a", "2a", "3a", "4a", "5a"],
                                    "lanes": [
                                        ["label": "Backup", "segments": [["from": 0.30, "to": 0.42, "tone": "bad", "label": "Locked"],
                                                                         ["from": 0.62, "to": 0.70, "tone": "good", "label": "Rerun"]]],
                                        ["label": "Report", "segments": [["from": 0.38, "to": 0.45, "tone": "bad"],
                                                                         ["from": 0.72, "to": 0.80, "tone": "good"]]],
                                        ["label": "Cleanup", "segments": [["from": 0.05, "to": 0.12, "tone": "good"]]],
                                        ["label": "Sync", "segments": [["from": 0.15, "to": 0.25, "tone": "good"], ["from": 0.85, "to": 0.92, "tone": "good"]]]],
                                    "note": "Red: both jobs waited on the same lock at 2:00."]
        var nightTasks = [
            task("night_q", job: night, title: "Check last night's jobs", age: 600,
                 result: ["spoken": "Two jobs failed on the same lock; I have a fix.", "label": "2 failures found",
                          "views": [nightQuestion, lanes]]),
        ]
        if setup.answers["night_q"] != nil {
            nightTasks.append(task("night_apply", job: night, title: "Apply the two fixes", status: "working", age: 8,
                                   short: "Moving the report job to 3:00", plan: ["Move the backup", "Move the report", "Dry run", "Confirm tonight's schedule"],
                                   milestones: ["Backup moved to 1:30"]))
        }

        // Rollout: the app update reached most devices.
        let devices: [String: Any] = ["kind": "devices", "title": "Devices", "items": [
            ["name": "Laptop", "value": "2.4", "tone": "good", "history": [1, 1, 2, 2, 3, 4]],
            ["name": "Desktop", "value": "2.4", "tone": "good", "history": [2, 2, 2, 3, 3, 3]],
            ["name": "Tablet", "value": "2.3", "tone": "warn", "detail": "Asleep"],
            ["name": "Studio", "value": "2.3", "tone": "warn", "detail": "Needs a restart"]]]
        let rollQuestion: [String: Any] = [
            "kind": "question", "question": "Update the last two devices now?",
            "context": "Two of four devices are on the new version. The other two are asleep or need a restart.",
            "why": "Nothing is running on them now, so a restart costs nothing.",
            "options": [
                ["text": "Update the last two now", "detail": "Restarts the app on both.", "risk": "low", "reversible": true],
                ["text": "Wait until tonight", "detail": "They update on their own at 2 am."]],
            "recommended": 0, "approvable": true]
        var rollTasks = [
            task("roll_q", job: roll, title: "Roll out the app update", age: 400, result: [
                "spoken": "Two of four devices are updated.", "label": "2 of 4 updated", "views": [rollQuestion, devices]]),
        ]
        if setup.answers["roll_q"] != nil {
            var after = devices
            after["items"] = [["name": "Laptop", "value": "2.4", "tone": "good"], ["name": "Desktop", "value": "2.4", "tone": "good"],
                              ["name": "Tablet", "value": "2.4", "tone": "good"], ["name": "Studio", "value": "2.4", "tone": "good"]]
            let before: [String: Any] = ["kind": "before_after", "title": "Version", "before": ["label": "Before", "text": "2 of 4 on 2.4"],
                                         "after": ["label": "After", "text": "All 4 on 2.4"]]
            rollTasks.append(task("roll_done", job: roll, title: "Update the last two devices", age: 20,
                                  plan: ["Wake the tablet", "Restart the studio app", "Install 2.4", "Check versions"],
                                  milestones: ["Tablet awake", "Studio app restarted", "2.4 installed on both", "All four report 2.4"],
                                  result: ["spoken": "All four devices are on 2.4.", "label": "All updated",
                                           "full": "Woke the tablet, restarted the studio app, installed 2.4 on both and checked all four devices report 2.4.",
                                           "views": [after, before],
                                           "shape": ["type": "rollout", "headline": "All four devices are on 2.4",
                                                     "checks": [["label": "devices updated", "value": "4 of 4", "good": true],
                                                                ["label": "errors", "value": "0", "good": true]],
                                                     "undo": true]]))
        }

        let routerQuestion: [String: Any] = [
            "kind": "question", "question": "When should the router stop sending work to a busy machine?",
            "options": [["text": "After 1 error, for 30 seconds", "risk": "low", "reversible": true],
                        ["text": "After 3 errors, for 30 seconds", "risk": "medium", "reversible": true], "Leave it"],
            "recommended": 0, "approvable": true]
        let routerTask = task("router_q", job: router, title: "Tune the router", age: 900,
                              result: ["spoken": "Which retry setting?", "label": "Setting to pick", "views": [routerQuestion]])
        let indexTask = task("index", job: ("index", "Search index rebuild"), title: "Rebuild the search index", status: "working", age: 12,
                             short: "Indexing documents, 61% done")
        let weather = task("weather", job: nil, title: "Weather tomorrow", age: 3000, result: ["spoken": "Sunny, 72.", "label": "Sunny, 72"])
        let notes = task("notes", job: ("notes", "Meeting notes"), title: "Tidy the meeting notes", age: 2500,
                         result: ["spoken": "Done.", "label": "Notes tidied"])
        let rollRunning = task("roll_run", job: ("app", "Docs refresh"), title: "Refresh the docs", status: "working", age: 6,
                               short: "Running the link checker", plan: ["Pull changes", "Rebuild pages", "Check links", "Publish preview", "Report"],
                               milestones: ["Pulled changes", "Pages rebuilt"])

        switch name {
        case "orb-idle":
            s.tasks = [weather]
        case "orb-talking":
            s.tasks = [weather]
            s.exchange = Exchange(you: "Can you check on", assistant: "", replyStarted: false)
        case "orb-working":
            s.tasks = [weather, rollRunning]
        case "orb-needs":
            s.tasks = [weather, rollRunning, routerTask] + relTasks
        case "orb-failed":
            s.tasks = [weather, task("sync_fail", job: ("sync", "Photo sync"), title: "Sync the photos", status: "failed", age: 60,
                                     failure: "The photo library was locked by another app.")]
        default:
            s.tasks = [weather, notes, routerTask] + nightTasks + [indexTask] + rollTasks + [rollRunning] + relTasks
        }
        s.workInfo = s.tasks.last?.info
        s.runID = s.workInfo?.runID
        s.work = deriveWork(s, now: now)
        return (s, false)
    }
}
