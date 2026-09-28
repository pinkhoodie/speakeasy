import XCTest
@testable import SpeakeasyCore

final class RoutingChoiceTests: XCTestCase {
    func testStatusCarriesTheRoutingChoices() throws {
        let json = """
        {"routing_model": "DeepSeek V4.1 Flash (Venice)", "routing_hint": "Switch it here",
         "routing_choice": {"current": "deepseek-v4.1-flash", "custom": "",
           "choices": [
             {"id": "default", "label": "Hermes default (your main model)", "note": "n", "available": true, "reason": ""},
             {"id": "deepseek-v4.1-flash", "label": "DeepSeek V4.1 Flash (Venice)", "note": "Fast", "available": true, "reason": ""},
             {"id": "claude-sonnet-5.5", "label": "Claude Sonnet 5.5 (Anthropic)", "note": "n", "available": false,
              "reason": "Needs Anthropic set up in Hermes"}]}}
        """
        let status = try JSONDecoder().decode(ServerStatus.self, from: Data(json.utf8))
        let routing = try XCTUnwrap(status.routingChoice)
        XCTAssertEqual(routing.current, "deepseek-v4.1-flash")
        XCTAssertEqual(routing.choices.map(\.id), ["default", "deepseek-v4.1-flash", "claude-sonnet-5.5"])
        XCTAssertFalse(routing.choices[2].available)
        XCTAssertEqual(routing.choices[2].reason, "Needs Anthropic set up in Hermes")
    }

    func testOlderServersWithoutChoicesStillDecode() throws {
        let status = try JSONDecoder().decode(ServerStatus.self, from: Data(#"{"routing_model": "anthropic · claude-opus-5-5"}"#.utf8))
        XCTAssertNil(status.routingChoice)
        XCTAssertEqual(status.routingModel, "anthropic · claude-opus-5-5")
    }

    func testSwitchBodyNamesTheModel() throws {
        let body = try RoutingChoices.postBody(model: "claude-sonnet-5.5")
        let object = try XCTUnwrap(try JSONSerialization.jsonObject(with: body) as? [String: String])
        XCTAssertEqual(object, ["model": "claude-sonnet-5.5"])
    }
}
