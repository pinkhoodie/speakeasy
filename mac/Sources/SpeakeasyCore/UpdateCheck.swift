import Foundation

/// "Check for Updates": compares this build's version with the latest GitHub release.
/// Pure parsing/comparison here; the network call lives in the app target.
public enum UpdateCheck {
    public static let repo = "rungmc357/speakeasy"
    public static let latestURL = URL(string: "https://api.github.com/repos/\(repo)/releases/latest")!
    public static let releasesPage = URL(string: "https://github.com/\(repo)/releases/latest")!

    public static let pluginManifestURL = URL(string: "https://raw.githubusercontent.com/\(repo)/main/plugin/speakeasy/plugin.yaml")!

    /// Read the published plugin's own version; a Mac-only release must not imply a plugin update.
    public static func parsePluginVersion(_ data: Data) -> String? {
        guard let text = String(data: data, encoding: .utf8) else { return nil }
        for line in text.components(separatedBy: .newlines) {
            let fields = line.split(separator: ":", maxSplits: 1)
            guard fields.count == 2, fields[0].trimmingCharacters(in: .whitespaces) == "version" else { continue }
            let value = fields[1].trimmingCharacters(in: .whitespacesAndNewlines)
            let core = value.split(separator: ".")
            return core.count == 3 && core.allSatisfy({ Int($0) != nil }) ? value : nil
        }
        return nil
    }

    public struct Release: Equatable, Sendable {
        public let version: String      // "0.2.0" (a leading "v" in the tag is dropped)
        public let pageURL: URL         // release notes page
        public let downloadURL: URL?    // the .dmg asset, when attached
        public init(version: String, pageURL: URL, downloadURL: URL?) {
            self.version = version; self.pageURL = pageURL; self.downloadURL = downloadURL
        }
    }

    public enum Outcome: Equatable, Sendable {
        case upToDate(current: String)
        case available(Release)
        case noReleases
    }

    /// Parse GitHub's `releases/latest` JSON. Drafts/prereleases never come from this endpoint.
    public static func parse(_ data: Data) -> Release? {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let tag = object["tag_name"] as? String,
              let page = (object["html_url"] as? String).flatMap(URL.init(string:)) else { return nil }
        let assets = object["assets"] as? [[String: Any]] ?? []
        // Prefer the fixed-name Speakeasy.dmg (what the website serves), so an update lands in Downloads
        // under the same name every time; fall back to any .dmg for releases without it.
        let dmgs = assets.filter { ($0["name"] as? String)?.lowercased().hasSuffix(".dmg") == true }
        let dmg = dmgs.first { ($0["name"] as? String) == "Speakeasy.dmg" } ?? dmgs.first
        let download = (dmg?["browser_download_url"] as? String).flatMap(URL.init(string:))
        return Release(version: normalize(tag), pageURL: page, downloadURL: download)
    }

    public static func outcome(current: String, latest: Release?) -> Outcome {
        guard let latest else { return .noReleases }
        return isNewer(latest.version, than: current) ? .available(latest) : .upToDate(current: current)
    }

    public static func pluginUpdate(latestVersion: String?, runningVersion: String?) -> String? {
        guard let latestVersion, let runningVersion, isNewer(latestVersion, than: runningVersion) else { return nil }
        return latestVersion
    }

    /// Numeric dotted comparison: "0.10.0" > "0.9.2"; missing parts count as 0; a non-numeric
    /// current version ("dev") is never considered newer than a release.
    public static func isNewer(_ candidate: String, than current: String) -> Bool {
        let a = parts(normalize(candidate)), b = parts(normalize(current))
        guard !a.isEmpty else { return false }
        guard !b.isEmpty else { return true }
        for i in 0..<max(a.count, b.count) {
            let x = i < a.count ? a[i] : 0, y = i < b.count ? b[i] : 0
            if x != y { return x > y }
        }
        return false
    }

    static func normalize(_ version: String) -> String {
        var v = version.trimmingCharacters(in: .whitespaces)
        if v.lowercased().hasPrefix("v") { v.removeFirst() }
        return v
    }

    private static func parts(_ version: String) -> [Int] {
        let core = version.split(separator: "-").first.map(String.init) ?? version
        let numbers = core.split(separator: ".").map { Int($0) }
        return numbers.contains(nil) ? [] : numbers.compactMap { $0 }
    }
}
