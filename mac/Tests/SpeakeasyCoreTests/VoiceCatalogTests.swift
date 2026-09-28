import XCTest
@testable import SpeakeasyCore

final class VoiceCatalogTests: XCTestCase {
    func testEachProviderOffersOnlyVoicesItAccepts() {
        let codex = Set(VoiceCatalog.codex.map(\.id)), api = Set(VoiceCatalog.openai.map(\.id))
        XCTAssertTrue(codex.isDisjoint(with: api))
        for provider in VoiceProvider.allCases {
            XCTAssertNotNil(VoiceCatalog.option(VoiceCatalog.defaultVoice(for: provider), provider: provider))
        }
    }

    func testSwitchingProviderDropsAVoiceTheNewProviderRejects() {
        XCTAssertNil(VoiceCatalog.compatible("marin", provider: .codex))
        XCTAssertEqual(VoiceCatalog.compatible("spruce", provider: .codex), "spruce")
        XCTAssertNil(VoiceCatalog.compatible(nil, provider: .openai))
    }

    func testEveryPreviewedVoiceHasABundledClipAndDescription() throws {
        let dir = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .appendingPathComponent("../../Resources/VoiceSamples").standardized
        for option in VoiceCatalog.codex + VoiceCatalog.openai where option.hasPreview {
            XCTAssertTrue(FileManager.default.fileExists(atPath: dir.appendingPathComponent("\(option.id).m4a").path), option.id)
            XCTAssertFalse(option.summary.isEmpty, option.id)
        }
    }
}
