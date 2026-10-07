import CoreGraphics
import ImageIO
import UniformTypeIdentifiers
import XCTest
@testable import SpeakeasyCore

final class AttachmentPolicyTests: XCTestCase {
    /// The image types ImageIO really decodes here, as the encoder passes them.
    private let imageTypes = Set(CGImageSourceCopyTypeIdentifiers() as? [String] ?? [])

    private func file(_ name: String) -> URL { URL(fileURLWithPath: "/Users/me/Desktop/\(name)") }

    // MARK: Sizes

    func testScalesARetinaCaptureToTheLongEdgeKeepingAspect() {
        let fitted = AttachmentPolicy.fittedSize(CGSize(width: 3024, height: 1964))
        XCTAssertEqual(fitted, CGSize(width: 1568, height: 1018))
        XCTAssertEqual(fitted.width / fitted.height, 3024.0 / 1964.0, accuracy: 0.002)
        XCTAssertEqual(AttachmentPolicy.fittedSize(CGSize(width: 1964, height: 3024)), CGSize(width: 1018, height: 1568),
                       "portrait scales by its height")
    }

    func testImageUnderTheCapKeepsItsSize() {
        XCTAssertEqual(AttachmentPolicy.fittedSize(CGSize(width: 800, height: 600)), CGSize(width: 800, height: 600))
        XCTAssertEqual(AttachmentPolicy.fittedSize(CGSize(width: 1568, height: 900)), CGSize(width: 1568, height: 900))
        XCTAssertEqual(AttachmentPolicy.fittedSize(.zero), .zero)
        XCTAssertEqual(AttachmentPolicy.fittedSize(CGSize(width: 10_000, height: 2)), CGSize(width: 1568, height: 1),
                       "a sliver never rounds to zero pixels")
    }

    func testCapsMatchThePlan() {
        XCTAssertEqual(AttachmentPolicy.maxAttachments, 3)
        XCTAssertEqual(AttachmentPolicy.maxLongEdge, 1568)
        XCTAssertEqual(AttachmentPolicy.targetImageBytes, 300_000)
        XCTAssertEqual(AttachmentPolicy.maxImageBytes, 2_500_000)
        XCTAssertEqual(AttachmentPolicy.maxRequestImageBytes, 6_500_000)
        XCTAssertEqual(AttachmentPolicy.maxFileBytes, 10_000_000)
        // Base64 grows bytes by 4/3: a full request of images stays under Hermes' 10 MB body limit.
        XCTAssertLessThan(AttachmentPolicy.maxRequestImageBytes * 4 / 3, 10_000_000)
    }

    // MARK: Quality ladder

    func testLadderStopsAtTheFirstQualityWithinTarget() throws {
        var tried: [Double] = []
        let data = try AttachmentPolicy.fitToBudget { quality in
            tried.append(quality)
            return Data(count: quality > 0.7 ? 500_000 : 280_000)
        }
        XCTAssertEqual(data.count, 280_000)
        XCTAssertEqual(tried, [0.82, 0.72, 0.62])
    }

    func testLadderStopsAtItsFloorAndReportsTooLargeInsteadOfLooping() {
        var calls = 0
        XCTAssertThrowsError(try AttachmentPolicy.fitToBudget { _ in calls += 1; return Data(count: 3_000_000) }) { error in
            XCTAssertEqual(error as? AttachmentFailure, .tooLarge)
        }
        XCTAssertEqual(calls, AttachmentPolicy.qualityLadder.count, "each step tried once, then it gives up")
        XCTAssertEqual(AttachmentPolicy.qualityLadder, AttachmentPolicy.qualityLadder.sorted(by: >), "quality only steps down")
    }

    func testLadderKeepsTheFloorResultWhenUnderTheHardCap() throws {
        let data = try AttachmentPolicy.fitToBudget { _ in Data(count: 900_000) }
        XCTAssertEqual(data.count, 900_000, "over the target but under 2.5 MB is still sent")
        XCTAssertThrowsError(try AttachmentPolicy.fitToBudget { _ in nil }) { error in
            XCTAssertEqual(error as? AttachmentFailure, .unsupported)
        }
    }

    // MARK: Password managers

    func testDenylistedBundleIDsAreRefusedOthersAccepted() {
        for id in ["com.1password.1password", "com.agilebits.onepassword7", "com.bitwarden.desktop",
                   "com.dashlane.dashlanephonefinal", "com.lastpass.LastPass", "com.apple.keychainaccess", "com.apple.Passwords"] {
            XCTAssertTrue(AttachmentPolicy.isPasswordManager(bundleID: id), id)
        }
        for id in ["com.apple.dt.Xcode", "com.apple.Safari", "com.apple.mail", "com.1password.safari.extension.not.an.app", ""] {
            XCTAssertFalse(AttachmentPolicy.isPasswordManager(bundleID: id), id)
        }
        XCTAssertFalse(AttachmentPolicy.isPasswordManager(bundleID: nil))
    }

    // MARK: Classification

    func testPicturesBecomeImagesDocumentsBecomeFiles() {
        for name in ["shot.png", "IMG_0001.HEIC", "photo.jpeg", "photo.jpg", "scan.tiff", "anim.gif", "pic.webp"] {
            XCTAssertEqual(AttachmentPolicy.classify(file(name), imageTypes: imageTypes), .success(.image), name)
        }
        for name in ["report.pdf", "notes.txt", "data.csv", "server.log", "logo.svg", "README"] {
            XCTAssertEqual(AttachmentPolicy.classify(file(name), imageTypes: imageTypes), .success(.file), name)
        }
        XCTAssertEqual(AttachmentPolicy.classify(file("blob"), contentType: .png, imageTypes: imageTypes), .success(.image),
                       "the file's own type wins over a missing extension")
    }

    func testFoldersPackagesAliasesAndLinksAreRefused() {
        let refused = Result<AttachmentItemKind, AttachmentFailure>.failure(.unsupported)
        XCTAssertEqual(AttachmentPolicy.classify(URL(fileURLWithPath: "/Users/me/Desktop/Project", isDirectory: true),
                                                 imageTypes: imageTypes), refused)
        XCTAssertEqual(AttachmentPolicy.classify(file("Keynote.app"), isRegularFile: false, imageTypes: imageTypes), refused)
        XCTAssertEqual(AttachmentPolicy.classify(file("Deck.key"), isRegularFile: false, imageTypes: imageTypes), refused)
        XCTAssertEqual(AttachmentPolicy.classify(file("shot alias.png"), isAlias: true, imageTypes: imageTypes), refused)
        XCTAssertEqual(AttachmentPolicy.classify(URL(string: "https://example.com/cat.png")!, imageTypes: imageTypes), refused)
    }

    func testFilesOverTenMegabytesAreTooLargeButBigPicturesAreReencoded() {
        XCTAssertEqual(AttachmentPolicy.classify(file("dump.pdf"), byteCount: 11_000_000, imageTypes: imageTypes), .failure(.tooLarge))
        XCTAssertEqual(AttachmentPolicy.classify(file("dump.pdf"), byteCount: 10_000_000, imageTypes: imageTypes), .success(.file))
        XCTAssertEqual(AttachmentPolicy.classify(file("raw.png"), byteCount: 11_000_000, imageTypes: imageTypes), .success(.image))
    }

    // MARK: Blank frames

    private func frame(width: Int = 1200, height: Int = 800, _ draw: (CGContext) -> Void) -> CGImage {
        let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
                                space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
        draw(context)
        return context.makeImage()!
    }

    private func fill(_ c: CGContext, _ gray: CGFloat, _ rect: CGRect) {
        c.setFillColor(CGColor(gray: gray, alpha: 1)); c.fill(rect)
    }

    func testSingleColorFrameIsBlank() {
        let black = frame { fill($0, 0, CGRect(x: 0, y: 0, width: 1200, height: 800)) }
        let white = frame { fill($0, 1, CGRect(x: 0, y: 0, width: 1200, height: 800)) }
        XCTAssertTrue(AttachmentPolicy.isBlank(black))
        XCTAssertTrue(AttachmentPolicy.isBlank(white))
        XCTAssertTrue(AttachmentPolicy.isNearlyUniform([UInt8](repeating: 37, count: 4096)))
        XCTAssertTrue(AttachmentPolicy.isNearlyUniform([]))
    }

    func testNormalScreenshotsAreNotBlank() {
        // A light window: title bar, sidebar, lines of text.
        let document = frame { c in
            fill(c, 1, CGRect(x: 0, y: 0, width: 1200, height: 800))
            fill(c, 0.93, CGRect(x: 0, y: 752, width: 1200, height: 48))
            fill(c, 0.96, CGRect(x: 0, y: 0, width: 220, height: 752))
            for y in stride(from: 700, to: 80, by: -28) { fill(c, 0.15, CGRect(x: 260, y: y, width: 600 + y % 250, height: 10)) }
        }
        // A dark window with nothing in it but its title bar.
        let emptyDark = frame { c in
            fill(c, 0.12, CGRect(x: 0, y: 0, width: 1200, height: 800))
            fill(c, 0.2, CGRect(x: 0, y: 752, width: 1200, height: 48))
        }
        // An empty white window whose only marks are its three window buttons.
        let emptyLight = frame { c in
            fill(c, 1, CGRect(x: 0, y: 0, width: 1200, height: 800))
            for (i, rgb) in [(1.0, 0.37, 0.34), (1.0, 0.74, 0.18), (0.16, 0.79, 0.25)].enumerated() {
                c.setFillColor(CGColor(red: rgb.0, green: rgb.1, blue: rgb.2, alpha: 1))
                c.fillEllipse(in: CGRect(x: 20 + i * 20, y: 772, width: 12, height: 12))
            }
        }
        XCTAssertFalse(AttachmentPolicy.isBlank(document))
        XCTAssertFalse(AttachmentPolicy.isBlank(emptyDark))
        XCTAssertFalse(AttachmentPolicy.isBlank(emptyLight))
    }

    // MARK: Failure reasons

    func testFailureReasonsAreThePluginsStrings() {
        XCTAssertEqual(AttachmentFailure.allCases.map(\.rawValue),
                       ["permission", "no_window", "speakeasy_window", "password_manager", "secure_input",
                        "blank", "too_large", "timeout", "unsupported"])
        XCTAssertEqual(AttachmentFailure(rawValue: "secure_input"), .secureInput)
    }

    // MARK: Window choice

    private let app: Int32 = 500
    private let stageManager: Int32 = 701
    private let panelService: Int32 = 900

    private func window(_ id: UInt32, pid: Int32, _ x: CGFloat, _ y: CGFloat, _ w: CGFloat, _ h: CGFloat,
                        layer: Int = 0, alpha: Double = 1) -> ScreenWindow {
        ScreenWindow(id: id, pid: pid, layer: layer, alpha: alpha, bounds: CGRect(x: x, y: y, width: w, height: h))
    }

    func testTakesTheAppsFrontmostWindowPastOtherProcessesAndPopovers() {
        let list = [
            window(1, pid: 422, 0, 0, 1470, 33, layer: 24),            // menu bar
            window(2, pid: 77, 300, 200, 600, 400, layer: 3),          // someone's floating panel
            window(3, pid: app, 500, 300, 40, 30),                     // a tiny popover of the app
            window(4, pid: app, 400, 300, 500, 300, alpha: 0),         // an invisible window of the app
            window(5, pid: stageManager, 16, 166, 137, 166),           // Stage Manager strip, same layer
            window(6, pid: app, 341, 141, 920, 436),                   // the app's window
            window(7, pid: app, 100, 100, 1000, 700),                  // the app's other window behind
        ]
        let pick = AttachmentPolicy.pickWindow(list, appPID: app)
        XCTAssertEqual(pick, WindowPick(main: 6, windows: [6], area: CGRect(x: 341, y: 141, width: 920, height: 436)))
        XCTAssertNil(AttachmentPolicy.pickWindow(list, appPID: 12345), "an app with no windows has nothing to capture")
    }

    func testASheetBringsItsWindowAndIsCroppedToTheirUnion() {
        let document = window(10, pid: app, 200, 100, 1000, 700)
        let list = [
            window(11, pid: app, 450, 128, 500, 320),                  // save sheet under the title bar
            window(12, pid: stageManager, 16, 166, 137, 166),
            document,
        ]
        let pick = AttachmentPolicy.pickWindow(list, appPID: app)
        XCTAssertEqual(pick?.main, 10, "the document window is the target, not the sheet")
        XCTAssertEqual(pick?.windows, [10, 11])
        XCTAssertEqual(pick?.area, document.bounds)
    }

    func testAnAlertOnASheetStillTargetsTheDocumentWindow() {
        let list = [
            window(21, pid: app, 560, 160, 280, 140),                  // alert on the sheet
            window(22, pid: app, 450, 128, 500, 320),                  // sheet
            window(23, pid: app, 200, 100, 1000, 700),                 // document
        ]
        let pick = AttachmentPolicy.pickWindow(list, appPID: app)
        XCTAssertEqual(pick?.main, 23)
        XCTAssertEqual(pick?.windows, [23, 21, 22])
    }

    func testOpenAndSavePanelServiceWindowsComeAlong() {
        let list = [
            window(31, pid: panelService, 450, 128, 500, 400),         // sandboxed app's save panel
            window(32, pid: panelService, 3000, 0, 500, 400),          // elsewhere: not on this window
            window(33, pid: app, 200, 100, 1000, 300),
        ]
        let pick = AttachmentPolicy.pickWindow(list, appPID: app, panelServicePIDs: [panelService])
        XCTAssertEqual(pick?.windows, [33, 31])
        XCTAssertEqual(pick?.area, CGRect(x: 200, y: 100, width: 1000, height: 428), "cropped to the union, panel included")
    }

    func testASeparateWindowInFrontIsNotASheet() {
        let list = [
            window(41, pid: app, 900, 500, 600, 400),                  // overlaps but sticks out: its own window
            window(42, pid: app, 200, 100, 1000, 700),
        ]
        XCTAssertEqual(AttachmentPolicy.pickWindow(list, appPID: app),
                       WindowPick(main: 41, windows: [41], area: CGRect(x: 900, y: 500, width: 600, height: 400)))
    }
}
