import Foundation

/// On-device task-title refinement for Speakeasy, mirroring Wing's TaskSummaryService
/// (Wing-Client/Wing/Services/TaskSummaryService.swift): a tight 3-word title plus a
/// representative emoji, generated locally with Apple's FoundationModels — no server
/// round trip. Returns nil below macOS 26 or when Apple Intelligence is unavailable;
/// callers keep whatever provisional label the plugin supplied. A later title arriving
/// from the plugin/Hermes titler chain should still win over a refined one, mirroring
/// Wing's "user rename wins" guard.
public enum TaskTitleService {
    /// Refine a spoken request into `(title, emoji)`. Emoji is nil when the model
    /// declined or its reply didn't parse as a single emoji cluster.
    public static func refine(request: String) async -> (title: String, emoji: String?)? {
        if #available(macOS 26.0, iOS 26.0, *) {
            return await FoundationTitleRefiner.shared.refine(request: request)
        }
        return nil
    }
}

#if canImport(FoundationModels)
import FoundationModels

@available(macOS 26.0, iOS 26.0, *)
private actor FoundationTitleRefiner {
    static let shared = FoundationTitleRefiner()

    private var isAvailable: Bool {
        SystemLanguageModel.default.availability == .available
    }

    func refine(request: String) async -> (title: String, emoji: String?)? {
        guard isAvailable else { return nil }
        async let titleAttempt = title(for: request)
        async let emojiAttempt = emoji(for: request)
        let (title, emoji) = await (titleAttempt, emojiAttempt)
        guard let title else { return nil }
        return (title, emoji)
    }

    private func title(for request: String) async -> String? {
        let prompt = """
        Create an exactly 3-word title for this conversation. Use lowercase except proper nouns. \
        Examples: "plan taco tour", "book train tickets", "research restaurants tokyo". \
        Only return the 3 words, nothing else.

        Messages:
        \(request)
        """
        let session = LanguageModelSession()
        guard let response = try? await session.respond(to: prompt) else { return nil }
        let title = response.content.trimmingCharacters(in: .whitespacesAndNewlines)
        return title.isEmpty ? nil : title
    }

    private func emoji(for request: String) async -> String? {
        let prompt = """
        Pick a single emoji that best represents this conversation's topic. \
        Reply with ONLY the emoji character — no text, no quotes, no punctuation, nothing else. \
        Examples: 🍕, ✈️, 💼, 🎬, 🏥, 📚, ⚽️, 🎨, 💰

        Messages:
        \(request)
        """
        let session = LanguageModelSession()
        guard let response = try? await session.respond(to: prompt) else { return nil }
        let raw = response.content.trimmingCharacters(in: .whitespacesAndNewlines)
        return Self.firstEmojiCluster(in: raw)
    }

    /// First grapheme cluster containing an emoji scalar — defensive against the model
    /// wrapping its answer in quotes or prefixing with "Emoji:".
    private static func firstEmojiCluster(in text: String) -> String? {
        for cluster in text {
            if cluster.unicodeScalars.contains(where: {
                $0.properties.isEmojiPresentation || ($0.properties.isEmoji && $0.value > 0x238C)
            }) {
                return String(cluster)
            }
        }
        return nil
    }
}
#endif
