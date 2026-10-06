import Foundation
import XCTest
@testable import SpeakeasyCore

/// Speech captured during call setup: the panel shows it's listening plus a live transcript.
final class EarlyListeningTests: XCTestCase {
    let t0 = Date(timeIntervalSince1970: 1_800_000_000)

    func run(_ events: [VoiceEvent], from state: VoiceState = VoiceState()) -> VoiceState {
        var s = state
        var now = t0
        for event in events { s = reduce(s, event, now: now); now += 0.1 }
        return s
    }

    func testConnectingWithoutEarlyListeningLooksAsBefore() {
        let p = present(run([.startRequested]))
        XCTAssertEqual(p.primary, "Connecting…")
        XCTAssertEqual(p.mark, .connecting)
        XCTAssertEqual(p.secondary, "Opening microphone")
    }

    func testListeningWhileConnectingInvitesSpeechThenShowsTheWords() {
        var s = run([.startRequested, .earlyListening(true)])
        var p = present(s)
        XCTAssertEqual(p.primary, "Listening")
        XCTAssertEqual(p.mark, .listening)
        XCTAssertEqual(p.secondary, "Go ahead · connecting")
        s = run([.earlyHeard("Turn on the bedroom lamps")], from: s)
        p = present(s)
        XCTAssertEqual(p.secondary, "\u{201C}Turn on the bedroom lamps\u{201D}")
    }

    func testLongWordsShowTheirEnd() {
        let long = String(repeating: "word ", count: 40) + "lamps"
        let p = present(run([.startRequested, .earlyListening(true), .earlyHeard(long)]))
        XCTAssertTrue(p.secondary.hasSuffix("lamps\u{201D}"))
        XCTAssertLessThan(p.secondary.count, 90)
    }

    func testMutedWhileConnectingSaysConnecting() {
        let p = present(run([.startRequested, .setMic(.muted), .earlyListening(true)]))
        XCTAssertEqual(p.primary, "Connecting…")
    }

    func testOnlyWhileConnecting() {
        XCTAssertFalse(run([.earlyListening(true)]).earlyListening)          // idle: never
        let live = run([.startRequested, .sessionAdmitted(interactionID: "vi_1"), .sessionStarted, .earlyListening(true)])
        XCTAssertFalse(live.earlyListening)
        XCTAssertEqual(present(live).primary, "Listening")                   // the normal live label
    }

    func testStoppingClearsTheWords() {
        let s = run([.startRequested, .earlyListening(true), .earlyHeard("lamps"), .earlyListening(false)])
        XCTAssertFalse(s.earlyListening)
        XCTAssertEqual(s.earlyHeard, "")
        XCTAssertEqual(present(s).primary, "Connecting…")
    }

    func testANewCallStartsClean() {
        let s = run([.startRequested, .earlyListening(true), .earlyHeard("lamps"), .endRequested, .startRequested])
        XCTAssertFalse(s.earlyListening)
        XCTAssertEqual(s.earlyHeard, "")
    }
}
