import XCTest
@testable import SpeakeasyCore

final class UpdateCheckTests: XCTestCase {
    func testVersionComparison() {
        XCTAssertTrue(UpdateCheck.isNewer("0.2.0", than: "0.1.0"))
        XCTAssertTrue(UpdateCheck.isNewer("v0.10.0", than: "0.9.2"))
        XCTAssertTrue(UpdateCheck.isNewer("1.0", than: "0.9.9"))
        XCTAssertFalse(UpdateCheck.isNewer("0.1.0", than: "0.1.0"))
        XCTAssertFalse(UpdateCheck.isNewer("0.1", than: "0.1.0"))
        XCTAssertFalse(UpdateCheck.isNewer("0.1.0", than: "0.2.0"))
        XCTAssertTrue(UpdateCheck.isNewer("0.2.0", than: "dev"), "a local dev build always sees a release as newer")
        XCTAssertFalse(UpdateCheck.isNewer("garbage", than: "0.1.0"))
    }

    func testPluginUpdateIndependentOfMacAppVersion() throws {
        let release = UpdateCheck.Release(version: "0.2.4",
                                          pageURL: URL(string: "https://example.com/release")!, downloadURL: nil)
        XCTAssertEqual(UpdateCheck.outcome(current: "0.2.4", latest: release), .upToDate(current: "0.2.4"))
        XCTAssertNil(UpdateCheck.pluginUpdate(latestVersion: "0.2.3", runningVersion: "0.2.3"),
                     "a Mac-only 0.2.4 release does not imply a plugin update")
        XCTAssertEqual(UpdateCheck.pluginUpdate(latestVersion: "0.2.5", runningVersion: "0.2.3"), "0.2.5")
        XCTAssertNil(UpdateCheck.pluginUpdate(latestVersion: "0.2.4", runningVersion: nil))
        XCTAssertNil(UpdateCheck.pluginUpdate(latestVersion: nil, runningVersion: "0.2.3"))
        XCTAssertEqual(UpdateCheck.parsePluginVersion(Data("name: speakeasy\nversion: 0.2.3\n".utf8)), "0.2.3")
        XCTAssertNil(UpdateCheck.parsePluginVersion(Data("version: something-wrong\n".utf8)))
    }

    func testParsesLatestReleaseAndPicksTheDmg() throws {
        let json = #"""
        {"tag_name":"v0.2.0","html_url":"https://github.com/rungmc357/speakeasy/releases/tag/v0.2.0",
         "assets":[{"name":"checksums.txt","browser_download_url":"https://example.com/c.txt"},
                   {"name":"Speakeasy-0.2.0.dmg","browser_download_url":"https://example.com/Speakeasy-0.2.0.dmg"}]}
        """#
        let release = try XCTUnwrap(UpdateCheck.parse(Data(json.utf8)))
        XCTAssertEqual(release.version, "0.2.0")
        XCTAssertEqual(release.downloadURL?.absoluteString, "https://example.com/Speakeasy-0.2.0.dmg")
        XCTAssertEqual(UpdateCheck.outcome(current: "0.1.0", latest: release), .available(release))
        XCTAssertEqual(UpdateCheck.outcome(current: "0.2.0", latest: release), .upToDate(current: "0.2.0"))
        XCTAssertEqual(UpdateCheck.outcome(current: "0.1.0", latest: nil), .noReleases)
        XCTAssertNil(UpdateCheck.parse(Data(#"{"message":"Not Found"}"#.utf8)))
    }
}
