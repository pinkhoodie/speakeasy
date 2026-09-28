import AppKit
import SwiftUI
import SpeakeasyCore

// MARK: - Image result cards

/// Thumbnail for one image a task produced. Bytes come only from the authenticated
/// api route for this exact run and card number; clicking opens the full image.
struct ImageResultCard: View {
    let runID: String
    let card: ImageCard
    /// Every image in the task, so the full-size window can step through them with arrow keys.
    var all: [ImageCard] = []
    @ObservedObject var model: VoicePanelModel
    @State private var image: NSImage?
    @State private var failed = false

    var body: some View {
        Button {
            if let image { openFullSize(image) }
        } label: {
            VStack(alignment: .leading, spacing: 5) {
                ZStack {
                    Color.primary.opacity(0.05)
                    if let image {
                        Image(nsImage: image).resizable().interpolation(.high).scaledToFit()
                    } else {
                        Image(systemName: failed ? "photo.badge.exclamationmark" : "photo")
                            .font(.system(size: 22)).foregroundStyle(.secondary)
                    }
                }
                .frame(maxWidth: .infinity).frame(height: 150)
                .clipShape(RoundedRectangle(cornerRadius: 7))
                HStack(spacing: 6) {
                    Text("\(card.number). \(card.name)").font(.system(size: 11)).lineLimit(1).truncationMode(.middle)
                    Spacer(minLength: 0)
                    Image(systemName: "arrow.up.left.and.arrow.down.right").font(.system(size: 10))
                }
                .foregroundStyle(.secondary)
            }
            .padding(8)
            .background(RoundedRectangle(cornerRadius: 9).fill(Color.primary.opacity(0.06)))
        }
        .buttonStyle(.plain)
        .disabled(image == nil)
        .help(image == nil ? (failed ? "Image unavailable" : "Loading image") : "Open full size")
        .accessibilityLabel("Image \(card.number): \(card.name), open full size")
        .task(id: "\(runID)/\(card.number)") {
            image = nil; failed = false
            if let data = await model.loadProductImage(runID, card.number), let loaded = NSImage(data: data) {
                image = loaded
            } else {
                failed = true
            }
        }
    }

    private func openFullSize(_ image: NSImage) {
        let images = all.isEmpty ? [card] : all, runID = runID, load = model.loadProductImage
        let start = images.firstIndex(where: { $0.number == card.number }) ?? 0
        ImagePreviewWindow.shared.show(image, title: card.name, index: start, count: images.count) { i in
            guard images.indices.contains(i), let data = await load(runID, images[i].number),
                  let loaded = NSImage(data: data) else { return nil }
            return (loaded, images[i].name)
        }
    }
}

// MARK: - Live view

/// What a running task is looking at right now. Refetched whenever the server's `seq` moves;
/// clicking opens it full size. `height` 34 makes the task-list thumbnail.
struct LiveImageView: View {
    let runID: String
    let live: LiveImage
    var height: CGFloat = 170
    var load: (String) async -> Data?
    @State private var image: NSImage?

    var body: some View {
        let small = height < 60
        Button {
            if let image { ImagePreviewWindow.shared.show(image, title: live.name) }
        } label: {
            ZStack {
                Color.primary.opacity(0.05)
                if let image {
                    Image(nsImage: image).resizable().interpolation(.high)
                        .aspectRatio(contentMode: small ? .fill : .fit)
                } else {
                    Image(systemName: "eye").font(.system(size: small ? 11 : 20)).foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: small ? height * 1.45 : .infinity).frame(height: height)
            .overlay(alignment: .bottomTrailing) {
                if !small && image != nil {
                    Image(systemName: "arrow.up.left.and.arrow.down.right")
                        .font(.system(size: 10, weight: .semibold)).foregroundStyle(.white)
                        .padding(5).background(Circle().fill(Color.black.opacity(0.45))).padding(6)
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: small ? 5 : 8))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(image == nil)
        .help(image == nil ? "Loading what it's looking at" : "\(live.label): \(live.name). Open full size")
        .accessibilityLabel("\(live.label): \(live.name)")
        .task(id: "\(runID)/\(live.seq)") {
            // Keep the previous frame on screen until the new one arrives.
            if let data = await load(runID), let loaded = NSImage(data: data) { image = loaded }
        }
    }
}

// MARK: - Full-size preview

/// One reusable floating window showing a result image at full size (scaled to fit the screen).
/// The image stays in memory: nothing is written to disk or handed to another app.
@MainActor
final class ImagePreviewWindow: NSObject, NSWindowDelegate {
    static let shared = ImagePreviewWindow()
    private var panel: NSPanel?

    /// Gallery state: how many images, which one is showing, and how to fetch another (nil = single image).
    private var count = 1
    private var index = 0
    private var fetch: ((Int) async -> (NSImage, String)?)?
    private var loading: Task<Void, Never>?

    func show(_ image: NSImage, title: String) {
        count = 1; index = 0; fetch = nil
        present(image, title: title, resize: true)
    }

    /// Several images: left/right arrow keys step through them (wrapping), fetched on demand.
    func show(_ image: NSImage, title: String, index: Int, count: Int, fetch: @escaping (Int) async -> (NSImage, String)?) {
        self.count = max(1, count); self.index = index; self.fetch = count > 1 ? fetch : nil
        present(image, title: title, resize: true)
    }

    fileprivate func step(_ delta: Int) {
        guard let fetch, count > 1 else { return }
        let next = ImageReviewLayout.step(index, by: delta, count: count)
        index = next
        loading?.cancel()
        loading = Task { @MainActor [weak self] in
            guard let loaded = await fetch(next), !Task.isCancelled, let self, self.index == next else { return }
            self.present(loaded.0, title: loaded.1, resize: false)
        }
    }

    private func present(_ image: NSImage, title: String, resize: Bool) {
        let panel = self.panel ?? makePanel()
        self.panel = panel
        let view = PreviewImageView(image: image)
        view.onArrow = { [weak self] delta in self?.step(delta) }
        view.imageScaling = .scaleProportionallyUpOrDown
        view.imageAlignment = .alignCenter
        view.animates = true
        view.setAccessibilityLabel(title)
        panel.contentView = view
        panel.title = count > 1 ? "\(title) (\(index + 1) of \(count))" : title
        if resize {
            panel.setContentSize(imagePreviewSize(for: image.size, screen: (NSScreen.main ?? NSScreen.screens.first)?.visibleFrame.size))
            panel.center()
        }
        NSApp.activate(ignoringOtherApps: true)
        panel.makeKeyAndOrderFront(nil)
        panel.makeFirstResponder(view)
    }

    private func makePanel() -> NSPanel {
        let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 480, height: 360),
                            styleMask: [.titled, .closable, .resizable, .utilityWindow],
                            backing: .buffered, defer: false)
        panel.isFloatingPanel = true
        panel.level = .floating
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.becomesKeyOnlyIfNeeded = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.delegate = self
        return panel
    }

    /// Preview smoke only: render the open window's content (title reported separately).
    func snapshot(to url: URL) throws {
        guard let panel, panel.isVisible, let view = panel.contentView,
              let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { throw CocoaError(.fileWriteUnknown) }
        view.cacheDisplay(in: view.bounds, to: rep)
        guard let png = rep.representation(using: .png, properties: [:]) else { throw CocoaError(.fileWriteUnknown) }
        try png.write(to: url)
        print("preview window title=\(panel.title) size=\(Int(panel.frame.width))x\(Int(panel.frame.height)) floating=\(panel.isFloatingPanel)")
    }

    func windowWillClose(_ notification: Notification) {
        loading?.cancel()
        fetch = nil
        panel?.contentView = nil  // release the image bytes
    }
}

/// The full-size image; takes key focus so left/right arrows step through a gallery.
private final class PreviewImageView: NSImageView {
    var onArrow: ((Int) -> Void)?
    override var acceptsFirstResponder: Bool { true }
    override func keyDown(with event: NSEvent) {
        let modified = !event.modifierFlags.intersection([.command, .option, .control]).isEmpty
        if let delta = ImageReviewLayout.arrowStep(keyCode: event.keyCode, commandOptionControl: modified) {
            onArrow?(delta)
        } else {
            super.keyDown(with: event)
        }
    }
}
