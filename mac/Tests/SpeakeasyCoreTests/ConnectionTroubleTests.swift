import XCTest
@testable import SpeakeasyCore

final class ConnectionTroubleTests: XCTestCase {
    let tailnet = URL(string: "https://hermes.example.ts.net:8795")!
    let other = URL(string: "http://127.0.0.1:8795")!

    func err(_ code: Int) -> Error { NSError(domain: NSURLErrorDomain, code: code) }

    func testHostNotFoundOnTailnetPointsAtTailscale() {
        let m = ConnectionTrouble.message(for: err(NSURLErrorCannotFindHost), server: tailnet)
        XCTAssertTrue(ConnectionTrouble.suggestsTailscale(m), m)
        XCTAssertTrue(ConnectionTrouble.suggestsTailscale(
            ConnectionTrouble.message(for: err(NSURLErrorTimedOut), server: tailnet)))
    }

    func testOfflineDeviceSaysOffline() {
        let m = ConnectionTrouble.message(for: err(NSURLErrorNotConnectedToInternet), server: tailnet)
        XCTAssertTrue(m.contains("offline"), m)
        XCTAssertFalse(ConnectionTrouble.suggestsTailscale(m))
    }

    func testNonTailnetServerDoesNotBlameTailscale() {
        let m = ConnectionTrouble.message(for: err(NSURLErrorCannotFindHost), server: other)
        XCTAssertFalse(ConnectionTrouble.suggestsTailscale(m), m)
    }

    func testOtherErrorsKeepTheirWording() {
        let e = NSError(domain: "Speakeasy", code: 1, userInfo: [NSLocalizedDescriptionKey: "Server said no"])
        XCTAssertEqual(ConnectionTrouble.message(for: e, server: tailnet), "Server said no")
    }
}
