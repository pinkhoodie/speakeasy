import XCTest
@testable import SpeakeasyCore

final class CardDeckTests: XCTestCase {
    private func task(_ id: String, run: String, result: [String: Any]?) -> TaskItem {
        var json: [String: Any] = ["task_id": id, "run_id": run, "status": "completed", "title": "Task \(id)"]
        if let result { json["result"] = result }
        return TaskItem(json: json)!
    }

    private let speakers: [String: Any] = ["full": "Picks below.", "cards": [
        ["name": "KEF LSX II LT", "url": "https://kef.com/lsx", "image_url": "https://kef.com/lsx.jpg",
         "price": "$999", "store": "KEF", "rating": "4.6"],
        ["name": "Bookshelf Speaker", "url": "https://example.com/speaker", "price": "$1,999"],
        ["name": "Naim Mu-so 2", "url": "https://naimaudio.com/muso"],
    ]]

    func testProductsWinOverLinksAndKeepSpokenNumbers() {
        let deck = CardDeck(task: task("t1", run: "r1", result: speakers))!
        XCTAssertTrue(deck.isProducts)
        XCTAssertEqual(deck.items.map(\.number), [1, 2, 3])
        XCTAssertEqual(deck.pillLabel, "3 products")
        XCTAssertEqual(deck.runID, "r1")
        XCTAssertEqual(deck.items[0].detail, "$999 · KEF · ★ 4.6")
        XCTAssertTrue(deck.items[0].hasImage)
        XCTAssertFalse(deck.items[1].hasImage)
        XCTAssertNil(deck.items[2].detail)
    }

    func testLinksFallbackShowsSiteOnlyWhenTitleDiffers() {
        let deck = CardDeck(task: task("t1", run: "r1", result: [
            "full": "Read [The review](https://www.whathifi.com/review) or https://rtings.com/x",
        ]))!
        XCTAssertFalse(deck.isProducts)
        XCTAssertEqual(deck.pillLabel, "2 links")
        XCTAssertEqual(deck.items[0].detail, "whathifi.com")
        XCTAssertNil(deck.items[1].detail, "a bare URL's title is already its site")
    }

    func testNoCardsNoDeck() {
        XCTAssertNil(CardDeck(task: task("t1", run: "r1", result: ["full": "It's 72 and sunny."])))
        XCTAssertNil(CardDeck(task: task("t1", run: "r1", result: nil)))
    }

    func testLatestAndAllPreferNewestTaskWithCards() {
        let tasks = [
            task("old", run: "r1", result: speakers),
            task("mid", run: "r2", result: ["full": "https://example.com/a"]),
            task("new", run: "r3", result: ["full": "No links here."]),
        ]
        XCTAssertEqual(CardDeck.latest(in: tasks)?.taskID, "mid")
        XCTAssertEqual(CardDeck.all(in: tasks).map(\.taskID), ["mid", "old"])
        XCTAssertNil(CardDeck.latest(in: []))
    }

    func testSingularLabels() {
        let one = CardDeck(task: task("t", run: "r", result: ["full": "https://example.com/a"]))!
        XCTAssertEqual(one.pillLabel, "1 link")
        let product = CardDeck(task: task("t", run: "r", result: ["cards": [["name": "A", "url": "https://a.example/x"]]]))!
        XCTAssertEqual(product.pillLabel, "1 product")
    }
}
