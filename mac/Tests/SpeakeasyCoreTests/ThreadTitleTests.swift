import XCTest
import SpeakeasyCore

final class ThreadTitleTests: XCTestCase {
    func testDecodesPendingTitles() throws {
        let json = """
        {"tasks": [{"task_id": "call_doc_1", "title": "Also separately, I want to use",
                    "request": "Also separately, I want to use Apple Intelligence to rename the title of previous chat threads",
                    "updated": 1792474018.0}]}
        """
        let pending = try XCTUnwrap(ThreadTitles.decodePending(Data(json.utf8)))
        XCTAssertEqual(pending.count, 1)
        XCTAssertEqual(pending[0], ThreadTitleCandidate(
            taskID: "call_doc_1", title: "Also separately, I want to use",
            request: "Also separately, I want to use Apple Intelligence to rename the title of previous chat threads",
            updated: 1792474018.0))
    }

    func testDecodeIgnoresAnythingUnexpected() {
        XCTAssertNil(ThreadTitles.decodePending(Data("{\"error\": \"not found\"}".utf8)))
        XCTAssertNil(ThreadTitles.decodePending(Data("nope".utf8)))
        XCTAssertNil(ThreadTitles.decodePending(Data("{\"tasks\": 3}".utf8)))
    }

    func testRenameBodyRoundTrip() throws {
        let data = try ThreadTitles.renameBody(taskID: "call_doc_1", title: "rename voice threads")
        let object = try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [String: String])
        XCTAssertEqual(object, ["task_id": "call_doc_1", "title": "rename voice threads"])
    }

    func testSanitizerCleansModelOutput() {
        XCTAssertEqual(ThreadTitles.sanitized("  plan taco tour "), "plan taco tour")
        XCTAssertEqual(ThreadTitles.sanitized("\u{201C}book train\ntickets\u{201D}"), "book train tickets")
        XCTAssertEqual(ThreadTitles.sanitized("research\r\nrestaurants   tokyo"), "research restaurants tokyo")
        XCTAssertNil(ThreadTitles.sanitized(nil))
        XCTAssertNil(ThreadTitles.sanitized(""))
        XCTAssertNil(ThreadTitles.sanitized("a"))
        XCTAssertNil(ThreadTitles.sanitized(String(repeating: "x", count: 91)))
    }
}
