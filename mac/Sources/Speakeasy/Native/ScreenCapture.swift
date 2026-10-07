import AppKit
import Darwin
import IOKit
import ScreenCaptureKit
import SpeakeasyClient
import SpeakeasyCore

/// One capture of the user's window, encoded for upload.
struct ScreenShot {
    let attachment: EncodedAttachment
    /// The captured app's name ("Xcode"), for the thumbnail and the one-time spoken notice.
    let appName: String
}

/// Captures the window the user is working in, with any sheet on it, for one Hermes request.
/// Never Speakeasy's own panel. Refuses, with an `AttachmentFailure` reason, while a regular
/// Speakeasy window is active, a password manager is frontmost, or the frontmost app holds secure
/// keyboard input. Nothing is kept. Mac app only: ScreenCaptureKit stays out of the shared client.
@MainActor
enum ScreenCapture {
    /// Longest a capture may take; the monthly macOS permission alert can stall it.
    static let timeLimit: TimeInterval = 5

    /// Screen Recording is allowed. Never prompts and never touches ScreenCaptureKit: probing it
    /// without permission shows the system dialog again every time.
    static var permitted: Bool { CGPreflightScreenCaptureAccess() }

    /// True when this process may show the permission prompt: the real app bundle, not a bare debug
    /// binary or a `--` tool run (smoke tests, snapshots, previews).
    static var mayPrompt: Bool {
        Bundle.main.bundleIdentifier != nil && Bundle.main.bundleURL.pathExtension == "app"
            && !CommandLine.arguments.dropFirst().contains { $0.hasPrefix("--") }
    }

    private static var asked = false

    /// Shows the system's Screen Recording prompt, at most once per launch (after a refusal, the
    /// switch is in System Settings). True when capture is already allowed; a new grant only takes
    /// effect after Speakeasy relaunches.
    @discardableResult
    static func requestPermission() -> Bool {
        if permitted { return true }
        guard mayPrompt, !asked else { return false }
        asked = true
        return CGRequestScreenCaptureAccess()
    }

    /// System Settings › Privacy & Security › Screen & System Audio Recording.
    static func openSettings() {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture") {
            NSWorkspace.shared.open(url)
        }
    }

    /// Captures the window now, or throws an `AttachmentFailure`.
    static func capture() async throws -> ScreenShot {
        guard permitted else { throw AttachmentFailure.permission }
        let deadline = Date().addingTimeInterval(timeLimit)
        let (app, pick) = try target()
        let content: SCShareableContent = try await within(deadline) { done in
            SCShareableContent.getExcludingDesktopWindows(true, onScreenWindowsOnly: true, completionHandler: done)
        }
        let (filter, config) = try setup(pick, content: content)
        let image: CGImage = try await within(deadline) { done in
            SCScreenshotManager.captureImage(contentFilter: filter, configuration: config, completionHandler: done)
        }
        let attachment = try await Task.detached(priority: .userInitiated) {
            if AttachmentPolicy.isBlank(image) { throw AttachmentFailure.blank }
            return try AttachmentEncoder.encode(image, name: "screen")
        }.value
        return ScreenShot(attachment: attachment, appName: app.localizedName ?? app.bundleIdentifier ?? "")
    }

    // MARK: Choosing the window

    /// The app the user is in and the window(s) to capture, or why not.
    private static func target() throws -> (NSRunningApplication, WindowPick) {
        // The panel never activates the app, so an active Speakeasy with a regular key or main
        // window means Settings, the image preview or onboarding is in use.
        if NSApp.isActive, [NSApp.keyWindow, NSApp.mainWindow].compactMap({ $0 }).contains(where: { !($0 is VoicePanel) }) {
            throw AttachmentFailure.speakeasyWindow
        }
        let windows = onScreenWindows()
        let ownPID = ProcessInfo.processInfo.processIdentifier
        var app = NSWorkspace.shared.frontmostApplication
        if app?.processIdentifier == ownPID {
            // Speakeasy is active with none of its windows up (say, Settings just closed): the user's
            // app is the regular app with the frontmost window.
            app = windows.lazy.filter { $0.pid != ownPID && $0.layer == 0 && $0.alpha > 0 }
                .compactMap { NSRunningApplication(processIdentifier: $0.pid) }
                .first { $0.activationPolicy == .regular }
        }
        guard let app else { throw AttachmentFailure.noWindow }
        if AttachmentPolicy.isPasswordManager(bundleID: app.bundleIdentifier) { throw AttachmentFailure.passwordManager }
        if secureInputOwners().contains(app.processIdentifier) { throw AttachmentFailure.secureInput }
        let services = Set(windows.map(\.pid)).subtracting([app.processIdentifier]).filter(isPanelService)
        guard let pick = AttachmentPolicy.pickWindow(windows, appPID: app.processIdentifier, panelServicePIDs: services) else {
            throw AttachmentFailure.noWindow
        }
        return (app, pick)
    }

    /// On-screen windows, front to back. Works without Screen Recording permission; only the
    /// titles are missing then, and they aren't used.
    private static func onScreenWindows() -> [ScreenWindow] {
        let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
        return list.compactMap { info in
            guard let id = info[kCGWindowNumber as String] as? Int,
                  let pid = info[kCGWindowOwnerPID as String] as? Int,
                  let layer = info[kCGWindowLayer as String] as? Int,
                  let bounds = (info[kCGWindowBounds as String] as? NSDictionary).flatMap({ CGRect(dictionaryRepresentation: $0) })
            else { return nil }
            return ScreenWindow(id: CGWindowID(id), pid: pid_t(pid), layer: layer,
                                alpha: info[kCGWindowAlpha as String] as? Double ?? 1, bounds: bounds)
        }
    }

    private static let panelServiceID = "com.apple.appkit.xpc.openAndSavePanelService"

    /// This process draws open and save panels for sandboxed apps; its windows float over the app's sheet.
    private static func isPanelService(_ pid: pid_t) -> Bool {
        if NSRunningApplication(processIdentifier: pid)?.bundleIdentifier == panelServiceID { return true }
        var path = [CChar](repeating: 0, count: Int(MAXPATHLEN))
        guard proc_pidpath(pid, &path, UInt32(path.count)) > 0 else { return false }
        return String(cString: path).contains("/\(panelServiceID).xpc/")
    }

    /// Processes holding secure keyboard input in this login session. The window server's session
    /// info and the console-user registry entry can name different processes (seen: loginwindow in
    /// one, the app showing a password field in the other), so both count.
    private static func secureInputOwners() -> Set<pid_t> {
        let key = "kCGSSessionSecureInputPID"
        guard let session = CGSessionCopyCurrentDictionary() as? [String: Any] else { return [] }
        var owners = Set<pid_t>()
        if let pid = session[key] as? Int, pid > 0 { owners.insert(pid_t(pid)) }
        let root = IORegistryGetRootEntry(kIOMainPortDefault)
        defer { IOObjectRelease(root) }
        let users = IORegistryEntryCreateCFProperty(root, "IOConsoleUsers" as CFString, kCFAllocatorDefault, 0)?
            .takeRetainedValue() as? [[String: Any]] ?? []
        if let audit = session["kCGSSessionAuditIDKey"] as? Int {
            for user in users where user["kCGSSessionAuditIDKey"] as? Int == audit {
                if let pid = user[key] as? Int, pid > 0 { owners.insert(pid_t(pid)) }
            }
        }
        return owners
    }

    // MARK: Capturing

    /// The filter and output size for `pick`: the plain window on its own, or, with a sheet or panel
    /// on it, those windows composited from their display and cropped to their union. Output pixels
    /// always set (ScreenCaptureKit's default is 1920×1080), long edge within the policy cap.
    private static func setup(_ pick: WindowPick, content: SCShareableContent) throws -> (SCContentFilter, SCStreamConfiguration) {
        let byID = Dictionary(content.windows.map { ($0.windowID, $0) }, uniquingKeysWith: { first, _ in first })
        guard let main = byID[pick.main] else { throw AttachmentFailure.noWindow }
        let config = SCStreamConfiguration()
        let filter: SCContentFilter
        let points: CGSize
        if pick.windows.count == 1 {
            filter = SCContentFilter(desktopIndependentWindow: main)
            points = filter.contentRect.isEmpty ? main.frame.size : filter.contentRect.size
        } else {
            func overlap(_ display: SCDisplay) -> CGFloat {
                let shared = CGDisplayBounds(display.displayID).intersection(pick.area)
                return shared.isNull ? 0 : shared.width * shared.height
            }
            guard let display = content.displays.max(by: { overlap($0) < overlap($1) }), overlap(display) > 0 else {
                throw AttachmentFailure.noWindow
            }
            let screen = CGDisplayBounds(display.displayID)
            let crop = pick.area.intersection(screen)
            filter = SCContentFilter(display: display, including: pick.windows.compactMap { byID[$0] })
            config.sourceRect = crop.offsetBy(dx: -screen.minX, dy: -screen.minY)
            config.ignoreShadowsDisplay = true
            points = crop.size
        }
        let scale = CGFloat(filter.pointPixelScale)
        let pixels = AttachmentPolicy.fittedSize(CGSize(width: points.width * scale, height: points.height * scale))
        config.width = Int(pixels.width)
        config.height = Int(pixels.height)
        config.showsCursor = false
        config.ignoreShadowsSingleWindow = true
        config.shouldBeOpaque = true
        return (filter, config)
    }

    /// Awaits a ScreenCaptureKit completion handler until `deadline`, then gives up with `.timeout`
    /// (the call can't be cancelled; a late answer is dropped). A Screen Recording refusal is
    /// `.permission`; any other error (the window closed, say) is `.noWindow`.
    nonisolated private static func within<T>(_ deadline: Date,
                                              _ call: (@escaping @Sendable (T?, Error?) -> Void) -> Void) async throws -> T {
        try await withCheckedThrowingContinuation { continuation in
            let once = Once(continuation)
            DispatchQueue.main.asyncAfter(deadline: .now() + max(0, deadline.timeIntervalSinceNow)) {
                once.finish(.failure(AttachmentFailure.timeout))
            }
            call { value, error in
                if let value {
                    once.finish(.success(value))
                } else if let error = error as? SCStreamError, error.code == .userDeclined {
                    once.finish(.failure(AttachmentFailure.permission))
                } else {
                    once.finish(.failure(AttachmentFailure.noWindow))
                }
            }
        }
    }
}

/// Resumes a continuation exactly once, whichever of the answer and the timeout comes first.
private final class Once<T>: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<T, Error>?

    init(_ continuation: CheckedContinuation<T, Error>) { self.continuation = continuation }

    func finish(_ result: Result<T, Error>) {
        lock.lock()
        let pending = continuation
        continuation = nil
        lock.unlock()
        pending?.resume(with: result)
    }
}
