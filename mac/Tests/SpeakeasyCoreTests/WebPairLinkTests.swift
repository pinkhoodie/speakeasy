import XCTest
@testable import SpeakeasyCore

final class WebPairLinkTests: XCTestCase {
    func testWebLinkFromChatParses() throws {
        let url = URL(string: "https://speakeasyvoice.ai/pair#server=https%3A%2F%2Fbox.tail123.ts.net%3A8795&code=123456")!
        let link = try PairingLink.parse(url).get()
        XCTAssertEqual(link.server.host, "box.tail123.ts.net")
        XCTAssertEqual(link.code, "123456")
    }

    func testWebLinkRejectsUntrustedServer() {
        let url = URL(string: "https://speakeasyvoice.ai/pair#server=http%3A%2F%2Fevil.example.com&code=123456")!
        XCTAssertEqual(PairingLink.parse(url), .failure(.untrustedServer))
    }

    func testOtherWebsitesAreNotPairingLinks() {
        let url = URL(string: "https://example.com/pair#server=https%3A%2F%2Fbox.tail123.ts.net&code=123456")!
        XCTAssertEqual(PairingLink.parse(url), .failure(.notSpeakeasy))
    }

    func testSetupPromptPointsAtTheAgentGuide() {
        XCTAssertTrue(SetupPrompt.text.contains("https://speakeasyvoice.ai/setup.md"))
    }
}

final class EmailDraftStatusTests: XCTestCase {
    func testAnEmailDraftReadsAsNeedingYouNotWaitingForHermes() {
        let draft = EmailDraft(draftID: "d", sha256: "0f", from: "a@example.com", to: ["b@example.com"], cc: [],
                               subject: "Hi", body: "Hello")
        var s = VoiceState()
        let now = Date()
        s.now = now
        s.delegationAt = now - 60
        s.workInfo = WorkInfo(runID: "run_x", status: "waiting_for_approval", updated: now, shortStatus: "Drafted an email",
                              updatedAt: now, statusSource: "authored", events: [], result: nil)
        s.workInfo?.emailDrafts = [draft]
        XCTAssertEqual(workStatusLine(deriveWork(s, now: now), now: now)?.0, "Needs your approval")
    }
}
