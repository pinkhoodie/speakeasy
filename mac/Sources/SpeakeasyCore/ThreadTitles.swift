import Foundation

/// One past voice thread the server still shows under its stand-in title (the label made from
/// the request's own words when the titler couldn't run). The Mac app renames these on device
/// with Apple Intelligence — the Wing thread-titler, applied to Speakeasy.
public struct ThreadTitleCandidate: Equatable, Decodable {
    public let taskID: String
    public let title: String
    public let request: String
    public let updated: Double

    public init(taskID: String, title: String, request: String, updated: Double) {
        self.taskID = taskID
        self.title = title
        self.request = request
        self.updated = updated
    }

    enum CodingKeys: String, CodingKey {
        case taskID = "task_id"
        case title, request, updated
    }
}

public enum ThreadTitles {
    /// Decodes GET /voice/titles. nil on anything unexpected (an older plugin without the
    /// route answers 404): the sweep then quietly does nothing.
    public static func decodePending(_ data: Data) -> [ThreadTitleCandidate]? {
        struct Payload: Decodable { let tasks: [ThreadTitleCandidate] }
        guard let payload = try? JSONDecoder().decode(Payload.self, from: data) else { return nil }
        return payload.tasks
    }

    /// The POST /voice/titles body for one rename.
    public static func renameBody(taskID: String, title: String) throws -> Data {
        struct Body: Encodable {
            let task_id: String
            let title: String
        }
        return try JSONEncoder().encode(Body(task_id: taskID, title: title))
    }

    /// A generated title as the server may store it: one line, no wrapping quotes, 2-90
    /// characters. nil when the model's answer is not usable as a title.
    public static func sanitized(_ raw: String?) -> String? {
        guard var title = raw?.trimmingCharacters(in: .whitespacesAndNewlines), !title.isEmpty else { return nil }
        title = title.replacingOccurrences(of: "\r", with: " ")
            .replacingOccurrences(of: "\n", with: " ")
        while title.contains("  ") { title = title.replacingOccurrences(of: "  ", with: " ") }
        let quotes: Set<Character> = ["\"", "'", "\u{201C}", "\u{201D}", "\u{2018}", "\u{2019}"]
        while let first = title.first, quotes.contains(first) { title.removeFirst() }
        while let last = title.last, quotes.contains(last) { title.removeLast() }
        title = title.trimmingCharacters(in: .whitespaces)
        guard title.count >= 2, title.count <= 90 else { return nil }
        return title
    }
}
