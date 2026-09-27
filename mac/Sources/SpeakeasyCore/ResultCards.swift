import Foundation

/// A destination found in a finished task's written answer. Cards are derived from
/// the answer, never from untrusted page content, and do not fetch remote pages.
public struct ResultCard: Equatable, Sendable, Identifiable {
    public let number: Int
    public let title: String
    public let url: URL
    public var id: Int { number }

    public static func links(in answer: String, limit: Int = 8) -> [ResultCard] {
        guard limit > 0 else { return [] }
        let capped = String(answer.prefix(32_000))
        let pattern = #"\[([^\]\n]{1,100})\]\((https?://[^\s)]+)\)|https?://[^\s<>\]\)]+"#
        guard let regex = try? NSRegularExpression(pattern: pattern, options: .caseInsensitive) else { return [] }
        let ns = capped as NSString
        var seen = Set<String>()
        var cards: [ResultCard] = []
        for match in regex.matches(in: capped, range: NSRange(location: 0, length: ns.length)) {
            let urlRange = match.range(at: 2).location != NSNotFound ? match.range(at: 2) : match.range
            var raw = ns.substring(with: urlRange).trimmingCharacters(in: CharacterSet(charactersIn: ".,;:!?"))
            // Bare URLs may end in a sentence's closing punctuation.
            while raw.hasSuffix(".") { raw.removeLast() }
            guard let url = URL(string: raw), let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
                  let scheme = components.scheme?.lowercased(), ["https", "http"].contains(scheme),
                  url.user == nil, url.password == nil,
                  let host = url.host?.lowercased(), host.contains("."), !host.hasSuffix(".local"),
                  !host.hasSuffix(".internal"), !host.hasSuffix(".test"), !host.hasSuffix(".invalid"),
                  host != "localhost", !host.hasPrefix("127."), !host.hasPrefix("10."),
                  !host.hasPrefix("192.168."), !host.hasPrefix("169.254."),
                  !host.hasPrefix("172."), !host.hasPrefix("["), !host.contains(":") else { continue }
            let key = url.absoluteString
            guard seen.insert(key).inserted else { continue }
            let named = match.range(at: 1).location != NSNotFound ? ns.substring(with: match.range(at: 1)) : ""
            let title = named.isEmpty ? host : named
            cards.append(ResultCard(number: cards.count + 1, title: title, url: url))
            if cards.count >= max(0, limit) { break }
        }
        return cards
    }
}
