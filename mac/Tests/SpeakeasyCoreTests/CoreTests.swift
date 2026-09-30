import Foundation
import XCTest
@testable import SpeakeasyCore

final class CoreTests: XCTestCase {
    func testVoicePageStringLiteralAcceptsTopLevelJSONFragments() {
        let url = jsonScriptLiteral("https://voice.tailnet.ts.net:8792")
        XCTAssertEqual(try? JSONSerialization.jsonObject(with: Data(url.utf8), options: [.fragmentsAllowed]) as? String,
                       "https://voice.tailnet.ts.net:8792")
        let token = jsonScriptLiteral("device\\\"token")
        XCTAssertEqual(try? JSONSerialization.jsonObject(with: Data(token.utf8), options: [.fragmentsAllowed]) as? String,
                       "device\\\"token")
    }

    func testClientConfigContainsNoProviderOrHermesCredentials() {
        let config = AppConfig.resolve(savedServer: nil, savedToken: nil, environment: [
            "SPEAKEASY_SERVER_URL": "https://voice.example.ts.net",
            "SPEAKEASY_DEVICE_TOKEN": "device-only",
            "OPENAI_API_KEY": "must-be-ignored",
            "API_SERVER_KEY": "must-be-ignored",
        ])
        XCTAssertEqual(config.serverURL?.absoluteString, "https://voice.example.ts.net")
        XCTAssertEqual(config.deviceToken, "device-only")
        XCTAssertTrue(config.isPaired)
        let labels = Mirror(reflecting: config).children.compactMap(\.label)
        XCTAssertEqual(labels.sorted(), ["deviceToken", "serverURL"])
    }

    func testSavedValuesAndUntrustedServerIgnored() {
        let saved = AppConfig.resolve(savedServer: "http://127.0.0.1:8795", savedToken: " tok \n", environment: [:])
        XCTAssertEqual(saved.serverURL?.host, "127.0.0.1")
        XCTAssertEqual(saved.deviceToken, "tok")
        let untrusted = AppConfig.resolve(savedServer: "https://example.com", savedToken: "tok", environment: [:])
        XCTAssertNil(untrusted.serverURL)
        XCTAssertFalse(untrusted.isPaired)
        XCTAssertFalse(AppConfig.resolve(savedServer: "http://127.0.0.1:8795", savedToken: "", environment: [:]).isPaired)
    }

    func testServerEndpointTrustBoundary() {
        XCTAssertNotNil(trustedServerBaseURL("http://127.0.0.1:8787"))
        XCTAssertNotNil(trustedServerBaseURL("https://voice.tailnet.ts.net"))
        XCTAssertNotNil(trustedServerBaseURL("https://100.101.102.103:8787"))
        XCTAssertNil(trustedServerBaseURL("http://100.101.102.103:8787"))
        XCTAssertNil(trustedServerBaseURL("https://example.com"))
        XCTAssertNil(trustedServerBaseURL("http://user:pass@127.0.0.1:8787"))
        XCTAssertNil(trustedServerBaseURL("http://127.0.0.1:8787?redirect=evil"))
        XCTAssertNil(trustedServerBaseURL("https://voice.tailnet.ts.net/path"))
    }

    func testSSEParserHandlesChunkBoundaries() {
        var parser = SSEParser()
        XCTAssertTrue(parser.append("event: run.com").isEmpty)
        let events = parser.append("pleted\ndata: {\"output\":\"done\"}\n\n")
        XCTAssertEqual(events, [SSEEvent(name: "run.completed", data: "{\"output\":\"done\"}")])
    }

    func testPCMConversionAndWAVHeader() {
        let samples: [Float] = [-1, 0, 1]
        let pcm = PCM.float32ToPCM16(samples)
        XCTAssertEqual(pcm.count, 6)
        let wav = PCM.wav(pcm16: pcm)
        XCTAssertEqual(String(decoding: wav.prefix(4), as: UTF8.self), "RIFF")
        XCTAssertEqual(String(decoding: wav.dropFirst(8).prefix(4), as: UTF8.self), "WAVE")
    }

    func testBriefTuneDecodes() throws {
        let json = #"{"state":"ready","calls":24,"summary":"Stale context.","edits":[{"id":"e1","kind":"add","section":"User","old":null,"new":"Assume home.","why":"It refused a distance.","evidence":"Mon"}],"product_issues":[{"what":"Slow status","evidence":"Tue"}]}"#
        let tune = try JSONDecoder().decode(BriefTune.self, from: Data(json.utf8))
        XCTAssertEqual(tune.state, "ready")
        XCTAssertEqual(tune.calls, 24)
        XCTAssertEqual(tune.edits.first?.section, "User")
        XCTAssertNil(tune.edits.first?.old)
        XCTAssertEqual(tune.productIssues.first?.what, "Slow status")
        let empty = try JSONDecoder().decode(BriefTune.self, from: Data(#"{"state":"none","calls":0}"#.utf8))
        XCTAssertTrue(empty.edits.isEmpty)
        let body = try JSONSerialization.jsonObject(with: BriefTune.applyBody(accept: ["e1", "e3"])) as? [String: [String]]
        XCTAssertEqual(body?["accept"], ["e1", "e3"])
    }
}