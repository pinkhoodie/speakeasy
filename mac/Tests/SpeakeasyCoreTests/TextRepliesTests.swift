import Foundation
import XCTest
@testable import SpeakeasyCore

/// A finished task's full written answer shows as a text reply in the expanded transcript only;
/// the voice keeps speaking (its words stay speech turns and the compact caption never shows the text).
final class TextRepliesTests: XCTestCase {
    let t0 = Date(timeIntervalSince1970: 1_800_000_000)
    let report = "Three flights fit.\n\n**UA 123** leaves 8:05, [details](https://example.com/ua123)."

    func live() -> VoiceState {
        var s = VoiceState()
        for e: VoiceEvent in [.startRequested, .sessionAdmitted(interactionID: "vi_1"), .sessionStarted] {
            s = reduce(s, e, now: t0)
        }
        return s
    }

    func task(_ id: String = "d1", _ status: String, full: String? = nil, spoken: String? = "Three flights fit.",
              seen: Bool = false) -> TaskItem {
        var info = WorkInfo(runID: "run_\(id)", status: status, updated: t0,
                            result: status == "completed" ? WorkResult(spoken: spoken, full: full) : nil,
                            title: "Flights to Denver")
        info.seen = seen
        return TaskItem(id: id, info: info)
    }

    func texts(_ s: VoiceState) -> [TranscriptTurn] { s.transcript.filter { $0.kind == .text } }

    func testFinishedTaskAddsOneTextReplyWithTheFullAnswer() {
        var s = reduce(live(), .inputDelta("Find me flights to Denver"), now: t0 + 1)
        s = reduce(s, .tasks([task("d1", "working")]), now: t0 + 2)
        XCTAssertTrue(texts(s).isEmpty)
        s = reduce(s, .tasks([task("d1", "completed", full: report)]), now: t0 + 10)
        XCTAssertEqual(texts(s).count, 1)
        let reply = texts(s)[0]
        XCTAssertEqual(reply.speaker, .assistant)
        XCTAssertEqual(reply.text, report)
        XCTAssertEqual(reply.cleanedText, report, "markdown link names survive (no speech-tag cleaning)")
        XCTAssertEqual(reply.taskID, "d1")
        XCTAssertEqual(reply.taskName, "Flights to Denver")
        // Later snapshots of the same finished task don't add it again.
        s = reduce(s, .tasks([task("d1", "completed", full: report)]), now: t0 + 11)
        s = reduce(s, .tasks([task("d1", "completed", full: report)]), now: t0 + 12)
        XCTAssertEqual(texts(s).count, 1)
    }

    func testVoiceKeepsSpeakingAndTheCompactCaptionNeverShowsTheText() {
        var s = reduce(live(), .inputDelta("Find me flights"), now: t0 + 1)
        s = reduce(s, .tasks([task("d1", "working")]), now: t0 + 2)
        s = reduce(s, .tasks([task("d1", "completed", full: report)]), now: t0 + 10)
        s = reduce(s, .outputDelta("Three flights fit."), now: t0 + 10.5)
        XCTAssertEqual(s.transcript.map(\.kind), [.speech, .text, .speech])
        XCTAssertEqual(s.transcript.last?.text, "Three flights fit.", "the spoken line is its own speech turn")
        XCTAssertEqual(s.exchange.assistant, "Three flights fit.", "compact caption shows only what was said")
        XCTAssertFalse(s.exchange.assistant.contains("UA 123"))
    }

    func testTextReplyLandingMidSentenceDoesNotSplitTheSpokenSentence() {
        var s = reduce(live(), .inputDelta("Flights?"), now: t0 + 1)
        s = reduce(s, .tasks([task("d1", "working")]), now: t0 + 2)
        s = reduce(s, .outputDelta("Still "), now: t0 + 3)
        s = reduce(s, .tasks([task("d1", "completed", full: report)]), now: t0 + 3.2)
        s = reduce(s, .outputDelta("checking."), now: t0 + 3.4)
        XCTAssertEqual(s.transcript.filter { $0.kind == .speech }.map(\.text), ["Flights?", "Still checking."])
        XCTAssertEqual(texts(s).count, 1)
    }

    func testUserSentenceContinuesAcrossATextReply() {
        var s = live()
        s = reduce(s, .tasks([task("d1", "working")]), now: t0 + 1)
        s = reduce(s, .inputDelta("And also "), now: t0 + 2)
        s = reduce(s, .tasks([task("d1", "completed", full: report)]), now: t0 + 2.2)
        s = reduce(s, .inputDelta("the hotel"), now: t0 + 2.4)
        XCTAssertEqual(s.transcript.filter { $0.speaker == .you }.map(\.text), ["And also the hotel"])
    }

    func testTaskFirstSeenAfterTheCallsFirstListStillCounts() {
        // A quick task can be finished before any running snapshot reaches the app.
        var s = reduce(live(), .tasks([]), now: t0 + 1)
        s = reduce(s, .tasks([task("d2", "completed", full: report)]), now: t0 + 5)
        XCTAssertEqual(texts(s).count, 1)
    }

    func testEarlierCallsCarriedTasksAreNotReplies() {
        // The first list of a call carries an earlier call's finished task (a draft or review): not an answer here.
        let s = reduce(live(), .tasks([task("old", "completed", full: report)]), now: t0 + 1)
        XCTAssertTrue(texts(s).isEmpty)
    }

    func testNoTextReplyWhenNothingMoreToRead() {
        var s = reduce(live(), .tasks([task("a", "working"), task("b", "working"), task("c", "working"),
                                       task("d", "working")]), now: t0 + 1)
        s = reduce(s, .tasks([
            task("a", "completed", full: "Lights off.", spoken: "Lights off.", seen: false),  // full == spoken
            task("b", "completed", full: "Done. Kitchen lights off.", seen: true),            // home command, seen
            task("c", "failed"),
            task("d", "completed", full: nil, spoken: "Done."),
        ]), now: t0 + 5)
        XCTAssertTrue(texts(s).isEmpty)
    }

    func testWorkOnlyViewAddsNoTextReplies() {
        var s = reduce(VoiceState(), .workOnlyOpened, now: t0)
        s = reduce(s, .tasks([task("d1", "working")]), now: t0 + 1)
        s = reduce(s, .tasks([task("d1", "completed", full: report)]), now: t0 + 2)
        XCTAssertTrue(texts(s).isEmpty)
    }

    func testTextRepliesSurvivePauseAndResume() {
        var s = reduce(live(), .tasks([task("d1", "working")]), now: t0 + 1)
        s = reduce(s, .tasks([task("d1", "completed", full: report)]), now: t0 + 2)
        s = reduce(s, .pauseRequested, now: t0 + 3)
        s = reduce(s, .sessionClosed, now: t0 + 4)
        XCTAssertEqual(s.connection, .paused)
        s = reduce(s, .resumeRequested, now: t0 + 5)
        s = reduce(s, .tasks([task("d1", "completed", full: report)]), now: t0 + 6)
        XCTAssertEqual(texts(s).count, 1)
    }

    func testANewCallStartsClean() {
        var s = reduce(live(), .tasks([task("d1", "working")]), now: t0 + 1)
        s = reduce(s, .tasks([task("d1", "completed", full: report)]), now: t0 + 2)
        s = reduce(s, .startRequested, now: t0 + 100)
        XCTAssertTrue(s.transcript.isEmpty)
        XCTAssertTrue(s.textRepliedTaskIDs.isEmpty)
        XCTAssertFalse(s.tasksBaselined)
    }

    func testTextReplyHelper() {
        XCTAssertNil(textReply(for: nil))
        XCTAssertNil(textReply(for: WorkResult(spoken: "Done.", full: "  Done. ")))
        XCTAssertEqual(textReply(for: WorkResult(spoken: "Short.", full: "Short.\nLong part.")), "Short.\nLong part.")
    }
}
