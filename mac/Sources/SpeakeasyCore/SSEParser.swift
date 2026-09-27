import Foundation

public struct SSEEvent: Equatable, Sendable {
    public var name: String
    public var data: String
    /// The `id:` field of this event, if the server sent one.
    public var id: String?
    public init(name: String, data: String, id: String? = nil) {
        self.name = name; self.data = data; self.id = id
    }
}

/// Incremental `text/event-stream` parser (event, data, id, retry, comments).
/// Tracks `lastEventID` so a reconnect can resume with a `Last-Event-ID` header.
public struct SSEParser: Sendable {
    private var buffer = ""
    /// The most recent `id:` seen; persists across events per the SSE spec.
    public private(set) var lastEventID: String?
    /// Server-requested reconnection delay in milliseconds, if any.
    public private(set) var retryMilliseconds: Int?

    public init(lastEventID: String? = nil) { self.lastEventID = lastEventID }

    public mutating func append(_ text: String) -> [SSEEvent] {
        buffer += text.replacingOccurrences(of: "\r\n", with: "\n")
        var events: [SSEEvent] = []
        while let range = buffer.range(of: "\n\n") {
            let block = String(buffer[..<range.lowerBound])
            buffer.removeSubrange(..<range.upperBound)
            var name = "message"
            var data: [String] = []
            var blockID: String?
            for line in block.split(separator: "\n", omittingEmptySubsequences: false) {
                if line.isEmpty || line.hasPrefix(":") { continue }
                let field: Substring
                var value: Substring
                if let colon = line.firstIndex(of: ":") {
                    field = line[..<colon]
                    value = line[line.index(after: colon)...]
                    if value.hasPrefix(" ") { value = value.dropFirst() }
                } else {
                    field = line; value = ""
                }
                switch field {
                case "event": name = value.isEmpty ? "message" : String(value)
                case "data": data.append(String(value))
                case "id": if !value.contains("\0") { blockID = String(value) }
                case "retry": if let ms = Int(value), ms >= 0 { retryMilliseconds = ms }
                default: break
                }
            }
            if let blockID { lastEventID = blockID.isEmpty ? nil : blockID }
            if !data.isEmpty {
                events.append(SSEEvent(name: name, data: data.joined(separator: "\n"), id: blockID))
            }
        }
        return events
    }

    public mutating func finish() -> [SSEEvent] { append("\n\n") }
}

/// Reconnect policy for the api event stream: exponential backoff capped at 8 s,
/// with a server `retry:` hint (capped at 30 s) as the floor.
public func sseReconnectDelay(attempt: Int, serverRetryMilliseconds: Int? = nil) -> TimeInterval {
    let base = min(8.0, 0.5 * pow(2.0, Double(max(0, attempt))))
    let hint = Double(serverRetryMilliseconds ?? 0) / 1000
    return max(base, min(hint, 30))
}
