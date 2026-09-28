import CoreGraphics

public enum EmailDraftLayout {
    /// Height for an expanded draft body. The rest of the panel (status, transcript, the card's
    /// header and buttons) takes roughly 480 pt, so the body gets what's left, between the
    /// collapsed height and 420.
    public static func expandedBodyHeight(visibleScreenHeight: CGFloat) -> CGFloat {
        min(420, max(160, visibleScreenHeight - 480))
    }
}
