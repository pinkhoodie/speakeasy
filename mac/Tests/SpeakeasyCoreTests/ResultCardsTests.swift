import XCTest
@testable import SpeakeasyCore

final class ResultCardsTests: XCTestCase {
    func testNumberedLinksAndDeduplication() {
        let cards = ResultCard.links(in: "[Speaker](https://example.com/speaker) and https://example.com/speaker plus https://other.example/item.")
        XCTAssertEqual(cards.map(\.number), [1, 2])
        XCTAssertEqual(cards.map(\.title), ["Speaker", "other.example"])
    }

    func testRejectsLocalAndNonWebDestinations() {
        let cards = ResultCard.links(in: "http://localhost/private https://10.0.0.2/a https://shop.example.com/a javascript:alert(1)")
        XCTAssertEqual(cards.count, 1)
    }

    func testProductDecodingAndFallback() {
        let info = WorkInfo(json: ["status": "completed", "result": ["full": "[A](https://example.com/a)", "cards": [
            ["name": "Speaker", "url": "https://example.com/a", "image_url": "https://example.com/a.jpg",
             "price": "$999", "store": "Example", "rating": "4.5", "specs": ["AirPlay", "Stereo"]]
        ]]])
        XCTAssertEqual(info?.products.first?.price, "$999")
        XCTAssertEqual(info?.products.first?.specs, ["AirPlay", "Stereo"])
        XCTAssertEqual(info?.products.first?.number, 1)
        XCTAssertEqual(info?.cards.first?.number, 1)
    }
}
