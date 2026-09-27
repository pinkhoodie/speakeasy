import XCTest
import CoreGraphics
@testable import SpeakeasyCore

final class ImageCardsTests: XCTestCase {
    func testImageCardsDecodeWithServerCardNumbers() {
        let info = WorkInfo(json: ["run_id": "run_1", "status": "completed", "result": [
            "full": "Here is concept 6.",
            "cards": [
                ["kind": "product", "name": "KEF", "url": "https://example.com/kef"],
                ["kind": "image", "name": "voxel-flame-concept-06.png"],
                ["kind": "image", "name": "  "],
            ]]])
        XCTAssertEqual(info?.products.map(\.number), [1])
        XCTAssertEqual(info?.images, [ImageCard(number: 2, name: "voxel-flame-concept-06.png"),
                                      ImageCard(number: 3, name: "Image 3")])
    }

    func testResultWithOnlyImagesIsKeptAndNeverBecomesAProduct() {
        let info = WorkInfo(json: ["status": "completed", "result": ["cards": [
            ["kind": "image", "name": "a.png", "url": "https://example.com/a", "path": "/Users/x/a.png"]]]])
        XCTAssertNotNil(info?.result)
        XCTAssertEqual(info?.images.count, 1)
        XCTAssertTrue(info?.products.isEmpty ?? false)
    }

    func testIgnoresCardsBeyondTheRouteLimitAndOtherKinds() {
        let cards: [[String: Any]] = (1...10).map { ["kind": "image", "name": "\($0).png"] } + [["kind": "video", "name": "v"]]
        let info = WorkInfo(json: ["status": "completed", "result": ["full": "x", "cards": cards]])
        XCTAssertEqual(info?.images.map(\.number), Array(1...8))
        XCTAssertNil(ImageCard(json: ["kind": "image", "name": "x"], number: 9))
        XCTAssertNil(ImageCard(json: ["kind": "product", "name": "x"], number: 1))
    }

    func testPreviewSizeFitsScreenAndKeepsAspect() {
        XCTAssertEqual(imagePreviewSize(for: CGSize(width: 800, height: 600), screen: CGSize(width: 1920, height: 1080)),
                       CGSize(width: 800, height: 600))
        let big = imagePreviewSize(for: CGSize(width: 4000, height: 2000), screen: CGSize(width: 2000, height: 1000))
        XCTAssertEqual(big, CGSize(width: 1700, height: 850))
        XCTAssertEqual(imagePreviewSize(for: CGSize(width: 32, height: 32), screen: nil), CGSize(width: 240, height: 240))
        XCTAssertEqual(imagePreviewSize(for: .zero, screen: nil), CGSize(width: 480, height: 360))
    }
}
