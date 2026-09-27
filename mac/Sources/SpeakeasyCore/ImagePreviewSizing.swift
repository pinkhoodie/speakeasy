import CoreGraphics

/// Size of the floating full-image preview: native size when it fits, else scaled
/// down to 85% of the visible screen, keeping the aspect ratio; never below 240pt.
public func imagePreviewSize(for image: CGSize, screen: CGSize?) -> CGSize {
    guard image.width > 0, image.height > 0 else { return CGSize(width: 480, height: 360) }
    let bounds = screen.map { CGSize(width: $0.width * 0.85, height: $0.height * 0.85) } ?? CGSize(width: 1200, height: 800)
    let scale = min(1, bounds.width / image.width, bounds.height / image.height)
    return CGSize(width: max(240, (image.width * scale).rounded()), height: max(240, (image.height * scale).rounded()))
}
