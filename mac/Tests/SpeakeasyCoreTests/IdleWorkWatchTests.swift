import XCTest
@testable import SpeakeasyCore

final class IdleWorkWatchTests: XCTestCase {
    func work(_ status: String, run: String = "run_1", spoken: String? = nil) -> WorkInfo {
        WorkInfo(runID: run, status: status, result: spoken.map { WorkResult(spoken: $0, full: "full") })
    }

    func testNoPollingWhenCallEndsWithTerminalOrNoWork() {
        var watch = IdleWorkWatch()
        watch.callEnded(lastWork: nil)
        XCTAssertNil(watch.nextDelay)
        watch.callEnded(lastWork: work("completed"))
        XCTAssertNil(watch.nextDelay)
        watch.callEnded(lastWork: work("ambiguous"))
        XCTAssertNil(watch.nextDelay)
    }

    func testPollsEvery20sUntilTerminalThenStops() {
        var watch = IdleWorkWatch()
        watch.callEnded(lastWork: work("working"))
        XCTAssertEqual(watch.nextDelay, 20)
        XCTAssertNil(watch.observe(work("working")))
        XCTAssertEqual(watch.nextDelay, 20)
        let notice = watch.observe(work("completed", spoken: "Your flight is on time."))
        XCTAssertEqual(notice, WorkNotice(kind: .completed, runID: "run_1", title: "Hermes", body: "Your flight is on time."))
        XCTAssertNil(watch.nextDelay)
        XCTAssertNil(watch.observe(work("completed")), "no polling or notices after terminal")
    }

    func testApprovalNotifiesOnceAndKeepsPolling() {
        var watch = IdleWorkWatch()
        watch.callEnded(lastWork: work("running"))
        XCTAssertEqual(watch.observe(work("waiting_for_approval"))?.body, "Needs your approval")
        XCTAssertNil(watch.observe(work("waiting_for_approval")))
        XCTAssertEqual(watch.nextDelay, 20)
        XCTAssertEqual(watch.observe(work("failed"))?.kind, .failed)
        XCTAssertNil(watch.nextDelay)
    }

    func testCancelledIsSilentAndTerminal() {
        var watch = IdleWorkWatch()
        watch.callEnded(lastWork: work("working"))
        XCTAssertNil(watch.observe(work("cancelled")))
        XCTAssertNil(watch.nextDelay)
    }

    func testExponentialBackoffResetsOnSuccess() {
        var watch = IdleWorkWatch()
        watch.callEnded(lastWork: work("working"))
        watch.observeError(); XCTAssertEqual(watch.nextDelay, 40)
        watch.observeError(); XCTAssertEqual(watch.nextDelay, 80)
        for _ in 0..<6 { watch.observeError() }
        XCTAssertEqual(watch.nextDelay, 300)
        _ = watch.observe(work("working"))
        XCTAssertEqual(watch.nextDelay, 20)
    }

    func testIgnoresOtherRunsAndStopsOnNewCall() {
        var watch = IdleWorkWatch()
        watch.callEnded(lastWork: work("working"))
        XCTAssertNil(watch.observe(work("completed", run: "run_other")))
        XCTAssertEqual(watch.nextDelay, 20)
        watch.stop()
        XCTAssertNil(watch.nextDelay)
        XCTAssertNil(watch.observe(work("completed")))
    }
}
