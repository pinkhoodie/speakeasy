import Foundation
import XCTest
@testable import SpeakeasyCore

final class EmailDraftTests: XCTestCase {
    let draftJSON: [String: Any] = [
        "draft_id": "d1", "sha256": "abc123", "from": "me@example.com",
        "to": ["a@example.com", "b@example.com"], "cc": ["c@example.com"], "bcc": [],
        "subject": "Lunch Friday", "body": "Hi,\nFriday works.\n", "status": "pending",
    ]

    func testDecodesDraftOnWorkAndTasks() {
        let work = WorkInfo(json: ["run_id": "r1", "status": "running", "email_drafts": [draftJSON, ["draft_id": "x"]]])!
        XCTAssertEqual(work.emailDrafts.count, 1, "a draft without sha256 is dropped")
        let d = work.emailDrafts[0]
        XCTAssertEqual(d.draftID, "d1"); XCTAssertEqual(d.sha256, "abc123")
        XCTAssertEqual(d.from, "me@example.com")
        XCTAssertEqual(d.to, ["a@example.com", "b@example.com"]); XCTAssertEqual(d.cc, ["c@example.com"]); XCTAssertEqual(d.bcc, [])
        XCTAssertEqual(d.subject, "Lunch Friday"); XCTAssertEqual(d.body, "Hi,\nFriday works.\n")
        XCTAssertEqual(d.status, .pending); XCTAssertTrue(d.isActionable)

        let tasks = TaskItem.list(json: [["task_id": "t1", "run_id": "r1", "status": "running", "email_drafts": [draftJSON]]])!
        var s = VoiceState()
        s.tasks = tasks
        XCTAssertEqual(s.pendingDrafts.map(\.draftID), ["d1"])
        XCTAssertTrue(panelNeedsAttention(s), "a pending draft holds the panel open")
        XCTAssertTrue(WorkInfo(json: ["status": "running"])!.emailDrafts.isEmpty)
    }

    func testStatusesAndTolerance() {
        for status in EmailDraft.Status.allCases {
            var o = draftJSON; o["status"] = status.rawValue
            XCTAssertEqual(EmailDraft(json: o)?.status, status)
        }
        var o = draftJSON; o["status"] = "mystery"; o["to"] = "solo@example.com"; o.removeValue(forKey: "cc")
        let d = EmailDraft(json: o)!
        XCTAssertEqual(d.status, .pending)
        XCTAssertEqual(d.to, ["solo@example.com"]); XCTAssertEqual(d.cc, [])
        XCTAssertEqual(EmailDraft(draftID: "a", sha256: "b", status: .approved).stateLabel, "Sending…")
        XCTAssertEqual(EmailDraft(draftID: "a", sha256: "b", status: .sent).stateLabel, "Sent")
        XCTAssertEqual(EmailDraft(draftID: "a", sha256: "b", status: .denied).stateLabel, "Discarded")
        XCTAssertEqual(EmailDraft(draftID: "a", sha256: "b", status: .revising).stateLabel, "Revising…")
        XCTAssertTrue(EmailDraft(draftID: "a", sha256: "b", status: .superseded).isCollapsed)
        XCTAssertFalse(EmailDraft(draftID: "a", sha256: "b", status: .sent).isActionable)
    }

    func testActionBodiesCarryOnScreenHash() throws {
        let d = EmailDraft(json: draftJSON)!
        let approve = try JSONSerialization.jsonObject(with: draftActionBody(.approve, draft: d)) as? [String: String]
        XCTAssertEqual(approve, ["action": "approve", "sha256": "abc123"])
        let revise = try JSONSerialization.jsonObject(with: draftActionBody(.revise, draft: d, instructions: " shorter ")) as? [String: String]
        XCTAssertEqual(revise, ["action": "revise", "sha256": "abc123", "instructions": "shorter"])
        XCTAssertThrowsError(try draftActionBody(.revise, draft: d, instructions: "  "))
        XCTAssertEqual(DraftAction.deny.optimisticStatus, .denied)
    }

    func testDraftsFlowThroughStreamEvents() {
        let payload = try! JSONSerialization.data(withJSONObject: [["task_id": "t1", "run_id": "r1", "status": "running",
                                                                    "email_drafts": [draftJSON]]])
        let events = parseServerStreamEvent(SSEEvent(name: "tasks", data: String(decoding: payload, as: UTF8.self)))
        guard case .tasks(let tasks)? = events.first else { return XCTFail("tasks event") }
        XCTAssertEqual(tasks.first?.info.emailDrafts.first?.subject, "Lunch Friday")
    }
}
