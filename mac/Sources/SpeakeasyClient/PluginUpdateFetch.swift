import Foundation
import SpeakeasyCore

/// Network half of the plugin-update notice (the parsing/comparison is `UpdateCheck` in the core).
/// Shared by the Mac and iPhone apps.
public enum PluginUpdateFetch {
    /// The newest published plugin version from the repo's `plugin.yaml`, or nil if it can't be read.
    public static func latestPluginVersion(userAgent: String, session: URLSession = .shared) async -> String? {
        var request = URLRequest(url: UpdateCheck.pluginManifestURL, timeoutInterval: 15)
        request.setValue(userAgent, forHTTPHeaderField: "User-Agent")
        guard let (data, response) = try? await session.data(for: request),
              (response as? HTTPURLResponse)?.statusCode == 200 else { return nil }
        return UpdateCheck.parsePluginVersion(data)
    }

    /// What to send a Hermes agent to update the plugin (the apps never update it themselves).
    public static let updateRequest = "Update the Speakeasy plugin on my Hermes installation. Check that it is enabled afterward, and tell me when I need to restart Hermes. Do not restart it yourself."
}
