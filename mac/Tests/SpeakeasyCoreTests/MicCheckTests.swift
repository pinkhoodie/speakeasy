import XCTest
@testable import SpeakeasyCore

/// "Listening" must mean the mic really gets through; a dead mic is caught and repaired.
final class MicCheckTests: XCTestCase {
    let t0 = Date(timeIntervalSince1970: 1_000)

    private func run(_ check: inout MicCheck, from: TimeInterval, to: TimeInterval, step: TimeInterval = 0.25,
                     level: Double?, packets: (TimeInterval) -> Int? = { Int($0 * 50) },
                     listening: Bool = true) -> MicFault? {
        var t = from
        while t <= to {
            if let fault = check.sample(level: level, packetsSent: packets(t), listening: listening,
                                        now: t0.addingTimeInterval(t)) { return fault }
            t += step
        }
        return nil
    }

    func testWorkingMicGoesFromCheckingToOk() {
        var c = MicCheck(); c.connectionOpened()
        XCTAssertEqual(c.health, .checking)
        XCTAssertNil(run(&c, from: 0, to: 10, level: 0.002))   // quiet room, but samples arrive
        XCTAssertEqual(c.health, .ok)
    }

    func testFlatZeroAtStartIsCaughtQuickly() {
        var c = MicCheck(); c.connectionOpened()
        XCTAssertEqual(run(&c, from: 0, to: 5, level: 0), .noSound)
        XCTAssertNotEqual(c.health, .ok)
    }

    func testMicDyingMidCallIsCaught() {
        var c = MicCheck(); c.connectionOpened()
        XCTAssertNil(run(&c, from: 0, to: 10, level: 0.01))
        XCTAssertEqual(c.health, .ok)
        XCTAssertEqual(run(&c, from: 10.25, to: 20, level: 0), .noSound)
    }

    func testAudioNotLeavingTheDeviceIsCaught() {
        var c = MicCheck(); c.connectionOpened()
        XCTAssertEqual(run(&c, from: 0, to: 6, level: 0.01, packets: { _ in 12 }), .notSending)
    }

    func testSpeechTheVoiceNeverHeardIsCaught() {
        var c = MicCheck(); c.connectionOpened()
        XCTAssertNil(run(&c, from: 0, to: 2, level: 0.2))        // 2s of clear speech
        XCTAssertEqual(run(&c, from: 2.25, to: 12, level: 0.003), .unanswered)
    }

    func testSpeechTheVoiceHeardIsFine() {
        var c = MicCheck(); c.connectionOpened()
        XCTAssertNil(run(&c, from: 0, to: 2, level: 0.2))
        c.heard()
        XCTAssertNil(run(&c, from: 2.25, to: 15, level: 0.003))
        // Once heard on this connection, later unanswered noise never triggers a reconnect.
        XCTAssertNil(run(&c, from: 15.25, to: 17, level: 0.3))
        XCTAssertNil(run(&c, from: 17.25, to: 30, level: 0.003))
    }

    func testPauseAfterBeingHeardIsNotADeadMic() {
        // AirPods send pure zeros while you pause; once the voice heard you, that's not a fault.
        var c = MicCheck(); c.connectionOpened()
        XCTAssertNil(run(&c, from: 0, to: 2, level: 0.2))
        c.heard()
        XCTAssertNil(run(&c, from: 2.25, to: 20, level: 0))
        XCTAssertEqual(c.health, .ok)
        // Audio that stops leaving the device is still caught.
        XCTAssertEqual(run(&c, from: 20.25, to: 30, level: 0.01, packets: { _ in 999 }), .notSending)
    }

    func testMutedOrAssistantTalkingIsNeverJudged() {
        var c = MicCheck(); c.connectionOpened()
        XCTAssertNil(run(&c, from: 0, to: 30, level: 0, packets: { _ in 0 }, listening: false))
    }

    func testUnknownStatsShapeIsNotTreatedAsDead() {
        var c = MicCheck(); c.connectionOpened()
        XCTAssertNil(run(&c, from: 0, to: 20, level: nil, packets: { _ in nil }))
        XCTAssertEqual(c.health, .ok)
    }

    func testGivesUpAfterTwoRepairsAndSaysSo() {
        var c = MicCheck()
        for attempt in 1...2 {
            c.connectionOpened()
            XCTAssertEqual(run(&c, from: 0, to: 5, level: 0), .noSound, "attempt \(attempt)")
            c.repairStarted()
            XCTAssertEqual(c.health, .repairing)
        }
        c.connectionOpened()
        XCTAssertNil(run(&c, from: 0, to: 5, level: 0))
        XCTAssertEqual(c.health, .broken)
    }

    func testPanelOnlySaysListeningOnceTheMicIsProven() {
        var s = VoiceState()
        s.connection = .live
        s.interactionID = "vi_1"
        s.micHealth = .checking
        XCTAssertEqual(present(s).primary, "Listening")   // no mic status flashes on a working call
        XCTAssertEqual(present(s).secondary, "Go ahead")
        s.micHealth = .ok
        XCTAssertEqual(present(s).primary, "Listening")
        s.micHealth = .broken
        XCTAssertEqual(present(s).primary, "Mic isn't working")
        XCTAssertEqual(present(s).secondary, "End the call and start a new one")
        s.connection = .ending; s.pausing = true; s.micHealth = .repairing
        XCTAssertEqual(present(s).primary, "Fixing the mic…")
    }
}
