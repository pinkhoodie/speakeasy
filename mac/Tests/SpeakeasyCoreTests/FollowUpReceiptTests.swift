import XCTest
@testable import SpeakeasyCore

/// Follow-up receipts and the same-project fence, as the apps decode and word them.
final class FollowUpReceiptTests: XCTestCase {
    func info(_ json: [String: Any]) -> WorkInfo { WorkInfo(json: json)! }

    func testFollowUpsDecodeWithTheirState() {
        let w = info(["status": "running", "follow_ups": [
            ["text": "fewer museums", "milestone": "You added: fewer museums", "state": "queued", "at": 1],
            ["text": "a day trip", "milestone": "You added: a day trip", "state": "landed"],
            ["text": "skip Sintra", "milestone": "You added: skip Sintra", "state": "not_read"],
            ["text": "bad", "milestone": "You added: bad", "state": "delivered"],   // unknown states are dropped
        ]])
        XCTAssertEqual(w.followUps.map(\.state), [.queued, .landed, .notRead])
        XCTAssertEqual(w.followUp(forStep: "You added: fewer museums")?.state, .queued)
        XCTAssertNil(w.followUp(forStep: "Read the page"))
    }

    func testQueuedNeverReadsAsReceived() {
        let queued = FollowUp(text: "x", milestone: "You added: x", state: .queued)
        XCTAssertTrue(queued.label.hasPrefix("Queued"))
        XCTAssertFalse(queued.label.lowercased().contains("read by"))
        XCTAssertEqual(FollowUp(text: "x", milestone: "m", state: .landed).label, "Read by the task")
        XCTAssertTrue(FollowUp(text: "x", milestone: "m", state: .notRead).label.hasPrefix("Not read"))
    }

    func testOlderPluginsSendNoReceipts() {
        XCTAssertEqual(info(["status": "running"]).followUps, [])
    }

    func testAWaitingTaskNamesWhatItWaitsFor() {
        let w = info(["status": "waiting_on_task", "waiting": ["name": "Fix the checkout bug", "project": "shop"]])
        XCTAssertEqual(w.waitingFor, "Fix the checkout bug")
        XCTAssertEqual(w.waitingProject, "shop")
        XCTAssertEqual(taskStatusLine(w, now: Date()).0, "Waiting for \u{201C}Fix the checkout bug\u{201D}")
        XCTAssertFalse(w.isTerminal)
        // Only a waiting task carries it.
        XCTAssertNil(info(["status": "running", "waiting": ["name": "x"]]).waitingFor)
    }
}
