import XCTest
@testable import SpeakeasyCore

/// Replies recorded from the real plugin server (plugin/tests/e2e_local.py with
/// SPEAKEASY_CONTRACT_DIR set). If the server's JSON changes, re-record them; these tests
/// then show exactly where the app stops understanding it.
final class ContractTests: XCTestCase {
    private func fixture(_ name: String) throws -> Data {
        let url = try XCTUnwrap(Bundle.module.url(forResource: name, withExtension: "json", subdirectory: "Contract"))
        return try Data(contentsOf: url)
    }

    func testStatus() throws {
        let s = try JSONDecoder().decode(ServerStatus.self, from: fixture("status"))
        XCTAssertEqual(s.assistantName, "Nova")
        XCTAssertEqual(s.provider, "codex")
        XCTAssertEqual(s.codexSignedIn, true)
        XCTAssertEqual(s.hermesAPIOK, true)
        XCTAssertEqual(s.threadsSupported, true)
        XCTAssertNotNil(s.voiceReady, "the server says whether a call can start")
    }

    func testSettings() throws {
        let s = try ServerSettings.decode(fixture("settings"))
        XCTAssertEqual(s.assistantName, "Nova")
        XCTAssertEqual(s.userName, "Sam")
        XCTAssertNil(s.deliveryTarget, "\"none\" means no delivery")
    }

    func testOnboarding() throws {
        XCTAssertFalse(OnboardingStatus.parse(try fixture("onboarding")).isComplete)
        let after = OnboardingStatus.parse(try fixture("onboarding_post"))
        XCTAssertTrue(after.isComplete)
        XCTAssertTrue(after.done.isSuperset(of: ["paired", "names_set", "delivery_set"]))
    }

    /// The bodies the app sends, written where the plugin's e2e test replays them against the real
    /// server (`app request accepted: ...`). Keeps the request side of the contract honest too.
    func testRecordAppRequests() throws {
        let dir = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .appendingPathComponent("Contract/requests", isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        func record(_ name: String, _ method: String, _ path: String, _ body: Data) throws {
            let object = try JSONSerialization.jsonObject(with: body)
            let spec: [String: Any] = ["method": method, "path": path, "body": object]
            try JSONSerialization.data(withJSONObject: spec, options: [.sortedKeys, .prettyPrinted])
                .write(to: dir.appendingPathComponent(name + ".json"))
        }
        var full = try ServerSettings.decode(fixture("settings"))
        try record("settings_patch", "PATCH", "/voice/settings", full.patchBody())
        full.delivery = .init(target: nil, newThreadPerTask: false)
        try record("settings_patch_no_delivery", "PATCH", "/voice/settings", full.patchBody())
        try record("onboarding_post", "POST", "/voice/onboarding",
                   OnboardingStatus.postBody(assistantName: "Nova", userName: "Sam", target: nil, writeBrief: false))
        let brief = """
        ## User
        Sam, a product designer in Lisbon. Call him Sam.
        ## Assistant persona
        Nova: calm, direct, a little dry. First person, no filler.
        ## Capability map
        Can check the calendar, draft email for approval, search the web and message Sam on Telegram.
        ## Answer preferences
        Short spoken answers, the key number first, details on screen.
        ## Current context
        Preparing a client pitch this week.
        """
        try record("brief_put", "PUT", "/voice/brief", VoiceBrief.putBody(text: brief))
    }

    func testDestinationsAndSuggestion() throws {
        let raw = try fixture("destinations")
        let list = Destination.list(raw)
        XCTAssertEqual(list.map(\.target), ["discord:900000000000000001", "discord:900000000000000002", "telegram:555000111"])
        XCTAssertEqual(list.first?.label, "Discord · Home / general")
        XCTAssertEqual(Destination.suggested(raw), "telegram:555000111")
    }

    func testBriefRoundTrip() throws {
        let b = try JSONDecoder().decode(VoiceBrief.self, from: fixture("brief"))
        XCTAssertEqual(b.state, "none")
        XCTAssertEqual(b.text, "")
        let body = try JSONSerialization.jsonObject(with: VoiceBrief.putBody(text: "About Sam")) as? [String: Any]
        XCTAssertEqual(Set(body?.keys ?? [:].keys), ["brief"], "PUT /voice/brief takes exactly {\"brief\": ...}")
        let withText = try JSONDecoder().decode(VoiceBrief.self, from: Data(#"{"brief":"About Sam","state":"edited","edited":true}"#.utf8))
        XCTAssertEqual(withText.text, "About Sam")
    }
}
