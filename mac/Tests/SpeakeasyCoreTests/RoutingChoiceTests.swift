import XCTest
@testable import SpeakeasyCore

final class RoutingChoiceTests: XCTestCase {
    func testStatusCarriesHermesProviders() throws {
        let json = """
        {"routing_model": "venice · deepseek-v4-1-flash (thinking off)",
         "routing_choice": {"current": {"provider": "venice", "model": "deepseek-v4-1-flash", "thinking": false, "is_default": false},
                            "label": "venice · deepseek-v4-1-flash (thinking off)",
                            "providers": [{"id": "anthropic", "name": "Anthropic", "models": ["claude-sonnet-5-5"]},
                                          {"id": "venice", "name": "Venice.ai", "models": ["deepseek-v4-1-flash"]}]}}
        """
        let status = try JSONDecoder().decode(ServerStatus.self, from: Data(json.utf8))
        let routing = try XCTUnwrap(status.routingChoice)
        XCTAssertEqual(routing.current.provider, "venice")
        XCTAssertFalse(routing.current.thinking)
        XCTAssertFalse(routing.current.isDefault)
        XCTAssertEqual(routing.providers.map(\.id), ["anthropic", "venice"])
        XCTAssertEqual(routing.providers[1].models, ["deepseek-v4-1-flash"])
    }

    func testOlderServersWithoutChoicesStillDecode() throws {
        let status = try JSONDecoder().decode(ServerStatus.self, from: Data(#"{"routing_model": "x · y"}"#.utf8))
        XCTAssertNil(status.routingChoice)
        XCTAssertEqual(status.routingModel, "x · y")
    }

    func testSwitchBodyNamesProviderModelAndThinking() throws {
        let body = try RoutingChoices.postBody(provider: "venice", model: "deepseek-v4-1-flash", thinking: false)
        let obj = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
        XCTAssertEqual(obj["provider"] as? String, "venice")
        XCTAssertEqual(obj["model"] as? String, "deepseek-v4-1-flash")
        XCTAssertEqual(obj["thinking"] as? Bool, false)
        let keep = try XCTUnwrap(JSONSerialization.jsonObject(
            with: try RoutingChoices.postBody(provider: "auto", model: "", thinking: nil)) as? [String: Any])
        XCTAssertNil(keep["thinking"])  // not mentioned: the server keeps the current setting
    }

    func testModelListDecodes() throws {
        let list = try ProviderModels.decode(Data(#"{"provider": "venice", "models": ["a", "b"]}"#.utf8))
        XCTAssertEqual(list.models, ["a", "b"])
    }
}
