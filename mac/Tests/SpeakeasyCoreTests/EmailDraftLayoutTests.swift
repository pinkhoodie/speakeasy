import XCTest
@testable import SpeakeasyCore

final class EmailDraftLayoutTests: XCTestCase {
    func testExpandedDraftFitsALaptopScreen() {
        // 13" MacBook Air at default scaling: ~ 855 pt visible below the menu bar.
        let body = EmailDraftLayout.expandedBodyHeight(visibleScreenHeight: 855)
        XCTAssertLessThanOrEqual(body + 480, 855)
        XCTAssertGreaterThan(body, 130)
    }

    func testBigScreensCapAndTinyScreensStillScroll() {
        XCTAssertEqual(EmailDraftLayout.expandedBodyHeight(visibleScreenHeight: 1400), 420)
        XCTAssertEqual(EmailDraftLayout.expandedBodyHeight(visibleScreenHeight: 500), 160)
    }
}
