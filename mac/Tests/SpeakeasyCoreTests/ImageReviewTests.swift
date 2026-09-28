import XCTest
@testable import SpeakeasyCore

final class ImageReviewTests: XCTestCase {
    private func task(_ id: String, run: String, images: Int, review: [Int], settled: Double, status: String = "completed") -> TaskItem {
        let cards: [[String: Any]] = (0..<images).map { ["kind": "image", "name": "d\($0).png"] }
        let info = WorkInfo(json: ["run_id": run, "status": status, "title": "Design \(id)",
                                   "result": ["full": "Done", "cards": cards],
                                   "review": ["images": review, "settled_at": settled]])!
        return TaskItem(id: id, info: info)
    }

    func testDecodesReviewAndBuildsCardFromImages() {
        let t = task("a", run: "run_a", images: 3, review: [1, 3], settled: 100)
        XCTAssertEqual(t.info.reviewImages, [1, 3])
        let review = t.info.review(taskID: "a", taskName: t.name)
        XCTAssertEqual(review?.images.map(\.number), [1, 3])
        XCTAssertEqual(review?.countLabel, "2 images")
        XCTAssertEqual(review?.taskName, "Design a")
    }

    func testNoReviewWhileRunningOrWithoutReviewField() {
        XCTAssertNil(task("a", run: "run_a", images: 2, review: [1], settled: 1, status: "working").info.review(taskID: "a", taskName: "x"))
        let plain = WorkInfo(json: ["run_id": "r", "status": "completed", "result": ["cards": [["kind": "image", "name": "x"]]]])!
        XCTAssertNil(plain.review(taskID: "a", taskName: "x"), "images alone are not a review; the server decides")
    }

    func testOnlyTheThreeMostRecentAndDismissedHidden() {
        var s = VoiceState()
        s.tasks = [task("a", run: "run_a", images: 1, review: [1], settled: 40),
                   task("b", run: "run_b", images: 1, review: [1], settled: 10),
                   task("c", run: "run_c", images: 1, review: [1], settled: 30),
                   task("d", run: "run_d", images: 2, review: [1, 2], settled: 20)]
        XCTAssertEqual(s.pendingReviews.map(\.runID), ["run_d", "run_c", "run_a"])
        XCTAssertTrue(panelNeedsAttention(s), "a waiting review holds the panel open like a draft")
        s = reduce(s, .reviewDismissed("run_d", [1, 2]), now: Date())
        XCTAssertEqual(s.pendingReviews.map(\.runID), ["run_b", "run_c", "run_a"])
        s = reduce(s, .reviewDismissed("run_a", [1]), now: Date())
        s = reduce(s, .reviewDismissed("run_b", [1]), now: Date())
        s = reduce(s, .reviewDismissed("run_c", [1]), now: Date())
        XCTAssertTrue(s.pendingReviews.isEmpty)
        XCTAssertFalse(panelNeedsAttention(s))
        XCTAssertEqual(s.tasks.count, 4, "dismissing a review keeps the task and its images")
    }

    func testDismissBodyAndLayout() throws {
        XCTAssertEqual(String(data: try reviewDismissBody(), encoding: .utf8), "{}")
        XCTAssertEqual(String(data: try reviewDismissBody(cards: [1, 2]), encoding: .utf8), "{\"cards\":[1,2]}")
        XCTAssertEqual(ImageReviewLayout.step(0, by: -1, count: 3), 2)
        XCTAssertEqual(ImageReviewLayout.step(2, by: 1, count: 3), 0)
        XCTAssertEqual(ImageReviewLayout.previewHeight(visibleScreenHeight: 1400, others: 0), 260)
        let laptop = ImageReviewLayout.previewHeight(visibleScreenHeight: 800, others: 2)
        XCTAssertEqual(laptop, 160)
        XCTAssertLessThanOrEqual(520 + laptop + 2 * 60, 800, "one open card plus two rows fit a laptop screen")
        XCTAssertEqual(ImageReviewLayout.previewHeight(visibleScreenHeight: 500, others: 2), 90)
        let reviews = ["a", "b", "c"].map { ImageReview(taskID: $0, runID: "run_\($0)", taskName: $0, images: []) }
        XCTAssertEqual(ImageReviewLayout.focused(reviews, picked: nil), "run_c", "the newest is open")
        XCTAssertEqual(ImageReviewLayout.focused(reviews, picked: "run_a"), "run_a")
        XCTAssertEqual(ImageReviewLayout.focused(reviews, picked: "run_gone"), "run_c")
    }

    func testLiveImageDecodesWithoutAnyPath() {
        let info = WorkInfo(json: ["run_id": "r", "status": "working",
                                   "live_image": ["name": "shot.png", "source": "screenshot", "seq": 3, "at": 100.0]])
        XCTAssertEqual(info?.liveImage, LiveImage(name: "shot.png", source: "screenshot", seq: 3, at: Date(timeIntervalSince1970: 100)))
        XCTAssertEqual(info?.liveImage?.label, "Looking at")
        XCTAssertNil(LiveImage(json: ["name": "x", "seq": 0]))
        XCTAssertNil(WorkInfo(json: ["status": "working"])?.liveImage)
    }

    func testShowEventParsesIdsOnly() {
        let event = SSEEvent(name: "show", data: #"{"task_id":"call_1","run_id":"run_1","image":"live","seq":2}"#)
        XCTAssertEqual(parseServerStreamEvent(event), [.show(ShowRequest(taskID: "call_1", runID: "run_1", image: .live, seq: 2))])
        XCTAssertEqual(parseServerStreamEvent(SSEEvent(name: "show", data: #"{"task_id":"x","image":"path"}"#)), [])
        var state = VoiceState()
        state = reduce(state, .show(ShowRequest(taskID: "call_1", runID: nil, image: .detail, seq: 1)), now: Date())
        XCTAssertEqual(state.showRequest?.image, .detail)
        XCTAssertTrue(state.showRequest!.opensDetail)
        XCTAssertFalse(ShowRequest(taskID: "t", runID: "r", image: .review, seq: 3).opensDetail)
    }
}
