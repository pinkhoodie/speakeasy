import AppKit
import Sparkle

/// In-place app updates (Sparkle). The feed is `appcast.xml` on the latest GitHub release; every
/// update is EdDSA-signed at release time and Sparkle refuses anything not signed with the key whose
/// public half is `SUPublicEDKey` in Info.plist. The user always approves: Sparkle shows the new
/// version and "Install Update"; the app then replaces itself and relaunches.
@MainActor
final class AppUpdater: NSObject, SPUUpdaterDelegate {
    static let shared = AppUpdater()
    private var controller: SPUStandardUpdaterController!
    /// `--update-smoke <feed>`: development-only end-to-end test of a real install. Uses the given
    /// feed, accepts the install without a click, relaunches. Signature checks still apply.
    private var smokeFeed: String?

    private override init() {
        super.init()
        // startingUpdater: false until start(), so dev builds and smoke runs never check by surprise.
        controller = SPUStandardUpdaterController(startingUpdater: false, updaterDelegate: self, userDriverDelegate: nil)
    }

    nonisolated func feedURLString(for updater: SPUUpdater) -> String? {
        MainActor.assumeIsolated { smokeFeed }
    }

    nonisolated func updater(_ updater: SPUUpdater, willInstallUpdateOnQuit item: SUAppcastItem,
                             immediateInstallationBlock immediateInstallHandler: @escaping () -> Void) -> Bool {
        let smoking = MainActor.assumeIsolated { smokeFeed != nil }
        guard smoking else { return false }
        print("update-smoke: installing \(item.displayVersionString) now"); fflush(stdout)
        immediateInstallHandler()
        return true
    }

    nonisolated func updater(_ updater: SPUUpdater, didAbortWithError error: Error) {
        let smoking = MainActor.assumeIsolated { smokeFeed != nil }
        if smoking { print("update-smoke: aborted: \(error.localizedDescription)"); fflush(stdout); exit(2) }
    }

    nonisolated func updaterDidNotFindUpdate(_ updater: SPUUpdater) {
        let smoking = MainActor.assumeIsolated { smokeFeed != nil }
        if smoking { print("update-smoke: no update found"); fflush(stdout); exit(3) }
    }

    func runSmoke(feed: String) {
        smokeFeed = feed
        print("update-smoke: running \(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") ?? "?"), feed \(feed)")
        fflush(stdout)
        do { try controller.updater.start() } catch { print("update-smoke: start failed: \(error)"); exit(4) }
        controller.updater.automaticallyDownloadsUpdates = true
        controller.updater.checkForUpdatesInBackground()
    }

    /// Only a signed build with a feed and a key can update itself.
    var isAvailable: Bool {
        let info = Bundle.main.infoDictionary ?? [:]
        return info["SUFeedURL"] != nil && info["SUPublicEDKey"] != nil
    }

    func start() {
        guard isAvailable else { return }
        do { try controller.updater.start() } catch { NSLog("Speakeasy: updater did not start: \(error)") }
    }

    /// Shows Sparkle's window: "up to date", or the new version with Install Update / Later.
    func checkForUpdates() {
        guard isAvailable else { return }
        NSApp.activate(ignoringOtherApps: true)
        controller.checkForUpdates(nil)
    }

    /// The "Check for updates automatically" preference (weekly; still asks before installing).
    var automaticallyChecks: Bool {
        get { controller.updater.automaticallyChecksForUpdates }
        set { controller.updater.automaticallyChecksForUpdates = newValue }
    }
}
