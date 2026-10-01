import XCTest
@testable import SpeakeasyCore

final class ViewCardsTests: XCTestCase {
    func testResultDecodesViewsAndSkipsUnknownKinds() {
        let json: [String: Any] = ["task_id": "t1", "status": "completed", "result": [
            "spoken": "Apple is at 333 dollars.",
            "views": [
                ["kind": "quote", "symbol": "AAPL", "name": "Apple", "price": 333.02, "change_pct": 1.1,
                 "points": [1, 2, 3]],
                ["kind": "hologram", "title": "from a newer server"],
                ["kind": "weather", "place": "Lisbon", "temp": 21, "unit": "°C", "condition": "clear"],
            ],
        ]]
        let task = TaskItem(json: json)
        XCTAssertEqual(task?.info.views.map(\.kind), ["quote", "weather"])
        XCTAssertEqual(task?.info.views.first?["points"].doubles, [1, 2, 3])
        XCTAssertEqual(task?.info.views.first?.summary, "Apple, $333.02, up 1.10%")
    }

    func testCardsAloneAreAResult() {
        let json: [String: Any] = ["task_id": "t2", "status": "completed",
                                   "result": ["views": [["kind": "fact", "title": "Height", "value": "828 m"]]]]
        XCTAssertNotNil(TaskItem(json: json)?.info.result)
    }

    func testOnlyHttpsUrlsLoad() {
        let card = ViewCard(kind: "place", data: ["name": "Cafe", "photo_url": "http://example.com/a.jpg",
                                                  "url": "https://example.com/cafe"])
        XCTAssertNil(card["photo_url"].url)
        XCTAssertEqual(card["url"].url?.host, "example.com")
    }

    func testBooleansStayBooleans() {
        let card = ViewCard(kind: "list", data: ["items": [["text": "Eggs", "done": true]]])
        XCTAssertEqual(card["items"].array.first?["done"].bool, true)
        XCTAssertNil(card["items"].array.first?["done"].double)
    }

    func testSignedChangeFormatting() {
        XCTAssertEqual(ViewCard.signed(-0.799, money: true), "−$0.80")
        XCTAssertEqual(ViewCard.signed(3.62, money: true), "+$3.62")
        XCTAssertEqual(ViewCard.signed(-0.24), "−0.24%")
    }
}
