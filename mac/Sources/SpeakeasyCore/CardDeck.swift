import Foundation

/// What the assistant put on screen for one task: product cards when the api sent
/// structured products, else numbered link cards found in the written answer.
/// Numbers match what the assistant'says aloud ("number two"), so they never renumber.
public struct CardDeck: Equatable, Sendable {
    public enum Item: Equatable, Sendable, Identifiable {
        case product(ProductCard)
        case link(ResultCard)

        public var id: Int { number }
        public var number: Int {
            switch self {
            case .product(let p): return p.number
            case .link(let l): return l.number
            }
        }
        public var title: String {
            switch self {
            case .product(let p): return p.name
            case .link(let l): return l.title
            }
        }
        public var url: URL {
            switch self {
            case .product(let p): return p.url
            case .link(let l): return l.url
            }
        }
        /// Short second line: price · store · rating for products, the site for links.
        public var detail: String? {
            switch self {
            case .product(let p):
                let parts = [p.price, p.store, p.rating.map { "★ \($0)" }].compactMap { $0 }.filter { !$0.isEmpty }
                return parts.isEmpty ? nil : parts.joined(separator: " · ")
            case .link(let l):
                let host = l.url.host?.lowercased() ?? ""
                let site = host.hasPrefix("www.") ? String(host.dropFirst(4)) : host
                return site == l.title.lowercased() ? nil : site
            }
        }
        public var hasImage: Bool {
            if case .product(let p) = self { return p.imageURL != nil }
            return false
        }
    }

    public let taskID: String
    public let runID: String?
    public let taskName: String
    public let items: [Item]
    public var isProducts: Bool { items.first.map { if case .product = $0 { return true } else { return false } } ?? false }

    public init?(task: TaskItem) {
        let products = task.info.products
        let items: [Item] = products.isEmpty ? task.info.cards.map(Item.link) : products.map(Item.product)
        guard !items.isEmpty else { return nil }
        taskID = task.id
        runID = task.info.runID
        taskName = task.name
        self.items = items
    }

    /// The most recent task that has cards (tasks are oldest-first).
    public static func latest(in tasks: [TaskItem]) -> CardDeck? {
        for task in tasks.reversed() { if let deck = CardDeck(task: task) { return deck } }
        return nil
    }

    /// Every task with cards, newest first (the iPad side panel lists them all).
    public static func all(in tasks: [TaskItem]) -> [CardDeck] {
        tasks.reversed().compactMap(CardDeck.init(task:))
    }

    /// One short phrase for the header pill: "3 products", "1 link".
    public var pillLabel: String {
        let n = items.count
        return isProducts ? (n == 1 ? "1 product" : "\(n) products") : (n == 1 ? "1 link" : "\(n) links")
    }
}
