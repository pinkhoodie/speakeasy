import Foundation

#if os(macOS) && canImport(FoundationModels)
import FoundationModels
#endif

/// On-device Apple Intelligence titler for Speakeasy's voice threads — ported from Wing's
/// `TaskSummaryService` (Wing-Client): FoundationModels when the system model is available,
/// exactly-3-word lowercase titles, and never an invented title when Apple Intelligence is
/// off, out of requests, or this device doesn't have it.
public actor ThreadTitler {
    public static let shared = ThreadTitler()

    /// Apple Intelligence ready on this Mac (model present, enabled, within its request limit).
    public static func isAvailable() -> Bool {
        #if os(macOS) && canImport(FoundationModels)
        if #available(macOS 26.0, *) {
            return SystemLanguageModel.default.availability == .available
        }
        #endif
        return false
    }

    /// Wing's prompt shape: exactly three words, lowercase except proper nouns, nothing else.
    public func generateTitle(for request: String) async -> String? {
        guard Self.isAvailable() else { return nil }
        #if os(macOS) && canImport(FoundationModels)
        if #available(macOS 26.0, *) {
            let material = request.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !material.isEmpty else { return nil }
            let prompt = """
            Create an exactly 3-word title for this voice request. Use lowercase except proper nouns. \
            Examples: "plan taco tour", "book train tickets", "research restaurants tokyo". \
            Only return the 3 words, nothing else.

            Request:
            \(material)
            """
            let session = LanguageModelSession()
            guard let response = try? await session.respond(to: prompt) else { return nil }
            let title = response.content.trimmingCharacters(in: .whitespacesAndNewlines)
            return title.isEmpty ? nil : title
        }
        #endif
        return nil
    }
}
