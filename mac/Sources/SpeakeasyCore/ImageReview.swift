import CoreGraphics
import Foundation

/// A finished task's images waiting on a review card in the call panel (`review` on a task row).
/// Like an email draft it stays across hang-ups until dismissed; dismissal goes to
/// `POST /voice/reviews/{run_id}/dismiss`. Image bytes come only from the authenticated
/// `/voice/card-image/{run_id}/{number}` route; the app never sees a path.
public struct ImageReview: Equatable, Sendable, Identifiable {
    public var taskID: String
    public var runID: String
    public var taskName: String
    public var images: [ImageCard]
    public var settledAt: Date?
    public var id: String { runID }

    public init(taskID: String, runID: String, taskName: String, images: [ImageCard], settledAt: Date? = nil) {
        self.taskID = taskID; self.runID = runID; self.taskName = taskName; self.images = images; self.settledAt = settledAt
    }

    /// "1 image" / "3 images".
    public var countLabel: String { images.count == 1 ? "1 image" : "\(images.count) images" }

    /// The review card shows at most this many (most recent); older ones stay inside their tasks.
    public static let maxShown = 3
}

extension WorkInfo {
    /// The review card for this task, if its finished images still wait for the user.
    public func review(taskID: String, taskName: String) -> ImageReview? {
        guard status == "completed", let runID, !reviewImages.isEmpty else { return nil }
        let wanted = Set(reviewImages)
        let shown = images.filter { wanted.contains($0.number) }
        guard !shown.isEmpty else { return nil }
        return ImageReview(taskID: taskID, runID: runID, taskName: taskName, images: shown, settledAt: reviewSettledAt)
    }
}

extension VoiceState {
    /// Up to three review cards, newest last, one per finished task with undismissed images.
    public var pendingReviews: [ImageReview] {
        var seen = Set<String>()
        let all = tasks.compactMap { task -> ImageReview? in
            guard var review = task.info.review(taskID: task.id, taskName: task.name) else { return nil }
            review.images.removeAll { dismissedReviews.contains("\(review.runID)|\($0.number)") }
            return review.images.isEmpty ? nil : review
        }.filter { seen.insert($0.runID).inserted }
        let ordered = all.enumerated().sorted { a, b in
            switch (a.element.settledAt, b.element.settledAt) {
            case let (x?, y?) where x != y: return x < y
            default: return a.offset < b.offset
            }
        }.map(\.element)
        return Array(ordered.suffix(ImageReview.maxShown))
    }
}

/// Body for `POST /voice/reviews/{run_id}/dismiss`: every image of the review (nil) or the listed ones.
public func reviewDismissBody(cards: [Int]? = nil) throws -> Data {
    var object: [String: Any] = [:]
    if let cards { object["cards"] = cards }
    return try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
}

public enum ImageReviewLayout {
    /// Preview height for the one open review card. The rest of the panel (header, captions, a task
    /// list, this card's title and buttons) takes roughly 520 pt and each other pinned card (a compact
    /// review row or a draft) about 60 pt, so the preview gets what's left, between 90 and 260: the
    /// card's buttons always stay on screen.
    public static func previewHeight(visibleScreenHeight: CGFloat, others: Int) -> CGFloat {
        min(260, max(90, visibleScreenHeight - 520 - 60 * CGFloat(max(0, others))))
    }

    /// Which pinned review is open: the one the user picked, else the newest.
    public static func focused(_ reviews: [ImageReview], picked: String?) -> String? {
        if let picked, reviews.contains(where: { $0.runID == picked }) { return picked }
        return reviews.last?.runID
    }

    /// The image shown after stepping `by` from `index` (wraps around).
    public static func step(_ index: Int, by delta: Int, count: Int) -> Int {
        guard count > 0 else { return 0 }
        return ((index + delta) % count + count) % count
    }
}
