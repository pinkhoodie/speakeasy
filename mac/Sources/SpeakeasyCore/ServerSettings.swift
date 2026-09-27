import Foundation

// Server-side settings, brief and status (`/voice/settings`, `/voice/brief`, `/voice/status`).
// Decoding is tolerant: unknown keys are ignored and every field is optional, so a
// newer or older server never breaks the app.

public enum VoiceProvider: String, Codable, CaseIterable, Sendable, Identifiable {
    case codex, openai
    public var id: String { rawValue }
    public var label: String {
        switch self {
        case .codex: return "ChatGPT sign-in (Codex)"
        case .openai: return "OpenAI API key"
        }
    }
}

public struct ServerSettings: Codable, Equatable, Sendable {
    public struct Voice: Codable, Equatable, Sendable {
        public var provider: String?
        public var voice: String?
        public init(provider: String? = nil, voice: String? = nil) { self.provider = provider; self.voice = voice }
    }
    public struct Brief: Codable, Equatable, Sendable {
        public var autoRefresh: Bool?
        public var includeRecentVoice: Bool?
        enum CodingKeys: String, CodingKey { case autoRefresh = "auto_refresh", includeRecentVoice = "include_recent_voice" }
        public init(autoRefresh: Bool? = nil, includeRecentVoice: Bool? = nil) {
            self.autoRefresh = autoRefresh; self.includeRecentVoice = includeRecentVoice
        }
    }

    /// Where finished work goes (a Hermes send target such as `telegram`); nil target = nowhere.
    public struct Delivery: Codable, Equatable, Sendable {
        public var target: String?
        public var newThreadPerTask: Bool?
        enum CodingKeys: String, CodingKey { case target, newThreadPerTask = "new_thread_per_task" }
        public init(target: String? = nil, newThreadPerTask: Bool? = nil) {
            self.target = target; self.newThreadPerTask = newThreadPerTask
        }
    }
    public struct Continuity: Codable, Equatable, Sendable {
        public var enabled: Bool?
        public init(enabled: Bool? = nil) { self.enabled = enabled }
    }

    public var assistantName: String?
    public var userName: String?
    public var voice: Voice?
    public var idlePauseMinutes: Double?
    /// Legacy single-target field; `delivery.target` wins when both are present.
    public var notifyTarget: String?
    public var delivery: Delivery?
    public var continuity: Continuity?
    public var instructionsExtra: String?
    public var brief: Brief?

    enum CodingKeys: String, CodingKey {
        case assistantName = "assistant_name", userName = "user_name", voice, idlePauseMinutes = "idle_pause_minutes"
        case notifyTarget = "notify_target", delivery, continuity, instructionsExtra = "instructions_extra", brief
    }

    public init(assistantName: String? = nil, userName: String? = nil, voice: Voice? = nil, idlePauseMinutes: Double? = nil,
                notifyTarget: String? = nil, delivery: Delivery? = nil, continuity: Continuity? = nil,
                instructionsExtra: String? = nil, brief: Brief? = nil) {
        self.assistantName = assistantName; self.userName = userName; self.voice = voice
        self.idlePauseMinutes = idlePauseMinutes; self.notifyTarget = notifyTarget; self.delivery = delivery
        self.continuity = continuity; self.instructionsExtra = instructionsExtra; self.brief = brief
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        assistantName = try? c.decodeIfPresent(String.self, forKey: .assistantName)
        userName = try? c.decodeIfPresent(String.self, forKey: .userName)
        voice = try? c.decodeIfPresent(Voice.self, forKey: .voice)
        idlePauseMinutes = try? c.decodeIfPresent(Double.self, forKey: .idlePauseMinutes)
        notifyTarget = try? c.decodeIfPresent(String.self, forKey: .notifyTarget)
        delivery = try? c.decodeIfPresent(Delivery.self, forKey: .delivery)
        continuity = try? c.decodeIfPresent(Continuity.self, forKey: .continuity)
        instructionsExtra = try? c.decodeIfPresent(String.self, forKey: .instructionsExtra)
        brief = try? c.decodeIfPresent(Brief.self, forKey: .brief)
    }

    /// The effective delivery target (nil = off).
    public var deliveryTarget: String? {
        let raw = delivery?.target ?? notifyTarget
        let trimmed = raw?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return trimmed.isEmpty || trimmed == "none" ? nil : trimmed
    }

    public static func decode(_ data: Data) throws -> ServerSettings {
        // Accept both a bare object and `{"settings": {...}}`.
        if let wrapped = try? JSONDecoder().decode([String: ServerSettings].self, from: data), let inner = wrapped["settings"] {
            return inner
        }
        return try JSONDecoder().decode(ServerSettings.self, from: data)
    }

    /// Body for `PATCH /voice/settings`: every known field (nested objects whole, so a
    /// shallow merge on the server cannot drop a sibling). Delivery target "none" = off.
    public func patchBody() throws -> Data {
        var object: [String: Any] = [:]
        if let assistantName { object["assistant_name"] = assistantName }
        if let userName { object["user_name"] = userName }
        if let voice {
            var v: [String: Any] = [:]
            if let p = voice.provider { v["provider"] = p }
            if let name = voice.voice { v["voice"] = name }
            object["voice"] = v
        }
        if let idlePauseMinutes { object["idle_pause_minutes"] = idlePauseMinutes }
        var d: [String: Any] = ["target": deliveryTarget ?? "none"]
        if let v = delivery?.newThreadPerTask { d["new_thread_per_task"] = v }
        object["delivery"] = d
        if let enabled = continuity?.enabled { object["continuity"] = ["enabled": enabled] }
        if let instructionsExtra { object["instructions_extra"] = instructionsExtra }
        if let brief {
            var b: [String: Any] = [:]
            if let v = brief.autoRefresh { b["auto_refresh"] = v }
            if let v = brief.includeRecentVoice { b["include_recent_voice"] = v }
            object["brief"] = b
        }
        return try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
    }

    // MARK: Resolved values with product defaults

    public var resolvedAssistantName: String {
        let name = assistantName?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return name.isEmpty ? VoiceState.defaultAssistantName : name
    }
    public var resolvedProvider: VoiceProvider { voice?.provider.flatMap(VoiceProvider.init(rawValue:)) ?? .codex }
    /// Idle auto-pause in seconds; 0 disables. Default 5 minutes.
    public var idleTimeout: TimeInterval {
        guard let minutes = idlePauseMinutes, minutes.isFinite, minutes >= 0 else { return VoiceState.defaultIdleTimeout }
        return min(minutes, 24 * 60) * 60
    }

    /// Voices offered in the picker. The server passes the name through to the provider.
    public static let knownVoices = ["alloy", "ash", "ballad", "cedar", "coral", "echo", "marin", "sage", "shimmer", "verse"]
}

/// `GET /voice/brief`.
public struct VoiceBrief: Codable, Equatable, Sendable {
    public var text: String
    /// e.g. `missing`, `writing`, `ready`, `failed` (server-defined; shown as-is).
    public var state: String?
    public var updatedAt: Date?
    public var edited: Bool

    /// The server sends and accepts the text as `brief` (docs/API.md); `text` is read for older replies.
    enum CodingKeys: String, CodingKey { case text = "brief", legacyText = "text", state, updatedAt = "updated_at", edited }

    public init(text: String = "", state: String? = nil, updatedAt: Date? = nil, edited: Bool = false) {
        self.text = text; self.state = state; self.updatedAt = updatedAt; self.edited = edited
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        text = (try? c.decodeIfPresent(String.self, forKey: .text))
            ?? (try? c.decodeIfPresent(String.self, forKey: .legacyText)) ?? ""
        state = try? c.decodeIfPresent(String.self, forKey: .state)
        edited = (try? c.decodeIfPresent(Bool.self, forKey: .edited)) ?? false
        if let seconds = try? c.decodeIfPresent(Double.self, forKey: .updatedAt) {
            updatedAt = Date(timeIntervalSince1970: seconds)
        } else if let text = try? c.decodeIfPresent(String.self, forKey: .updatedAt) {
            updatedAt = ISO8601DateFormatter.flexible(text)
        } else {
            updatedAt = nil
        }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(text, forKey: .text)
        try c.encodeIfPresent(state, forKey: .state)
        try c.encodeIfPresent(updatedAt?.timeIntervalSince1970, forKey: .updatedAt)
        try c.encode(edited, forKey: .edited)
    }

    public static func putBody(text: String) throws -> Data {
        try JSONSerialization.data(withJSONObject: ["brief": text], options: [])
    }
}

/// `GET /voice/status`.
public struct ServerStatus: Codable, Equatable, Sendable {
    public var assistantName: String?
    public var provider: String?
    public var codexSignedIn: Bool?
    public var apiKeySet: Bool?
    public var briefState: String?
    public var hermesAPIOK: Bool?
    public var version: String?
    /// The delivery platform can open a thread per task.
    public var threadsSupported: Bool?

    enum CodingKeys: String, CodingKey {
        case assistantName = "assistant_name", provider, codexSignedIn = "codex_signed_in"
        case apiKeySet = "api_key_set", briefState = "brief_state", hermesAPIOK = "hermes_api_ok", version
        case threadsSupported = "threads_supported"
    }

    public init(assistantName: String? = nil, provider: String? = nil, codexSignedIn: Bool? = nil, apiKeySet: Bool? = nil,
                briefState: String? = nil, hermesAPIOK: Bool? = nil, version: String? = nil) {
        self.assistantName = assistantName; self.provider = provider; self.codexSignedIn = codexSignedIn
        self.apiKeySet = apiKeySet; self.briefState = briefState; self.hermesAPIOK = hermesAPIOK; self.version = version
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        assistantName = try? c.decodeIfPresent(String.self, forKey: .assistantName)
        provider = try? c.decodeIfPresent(String.self, forKey: .provider)
        codexSignedIn = try? c.decodeIfPresent(Bool.self, forKey: .codexSignedIn)
        apiKeySet = try? c.decodeIfPresent(Bool.self, forKey: .apiKeySet)
        briefState = try? c.decodeIfPresent(String.self, forKey: .briefState)
        hermesAPIOK = try? c.decodeIfPresent(Bool.self, forKey: .hermesAPIOK)
        version = try? c.decodeIfPresent(String.self, forKey: .version)
        threadsSupported = try? c.decodeIfPresent(Bool.self, forKey: .threadsSupported)
    }

    public var resolvedAssistantName: String {
        let name = assistantName?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return name.isEmpty ? VoiceState.defaultAssistantName : name
    }

    public struct Check: Equatable, Sendable, Identifiable {
        public var id: String
        public var ok: Bool
        public var title: String
        /// What to do about it, when not ok.
        public var fix: String?
    }

    public var resolvedProvider: VoiceProvider { provider.flatMap(VoiceProvider.init(rawValue:)) ?? .codex }

    /// The checklist the first-run screen and Settings › Connection show.
    public var checks: [Check] {
        var out: [Check] = []
        out.append(Check(id: "hermes", ok: hermesAPIOK == true, title: "Hermes API server",
                         fix: hermesAPIOK == true ? nil : "Turn on the Hermes API server: run hermes voice setup"))
        switch provider.flatMap(VoiceProvider.init(rawValue:)) ?? .codex {
        case .codex:
            out.append(Check(id: "voice", ok: codexSignedIn == true, title: "ChatGPT sign-in",
                             fix: codexSignedIn == true ? nil : "Sign in to ChatGPT: run codex login"))
        case .openai:
            out.append(Check(id: "voice", ok: apiKeySet == true, title: "OpenAI API key",
                             fix: apiKeySet == true ? nil : "Add an OpenAI API key: run hermes voice config"))
        }
        let briefReady = briefState == "ready" || briefState == "edited"
        out.append(Check(id: "brief", ok: briefReady, title: "Voice brief",
                         fix: briefReady ? nil : (briefState == "writing"
                            ? "Hermes is writing the voice brief; calls work meanwhile"
                            : "No voice brief yet; calls work, or write one in Settings › Voice brief")))
        return out
    }

    /// Calls can start (the brief is optional).
    public var readyForCalls: Bool { checks.filter { $0.id != "brief" }.allSatisfy(\.ok) }
}

/// One entry of `GET /voice/destinations`: a connected Hermes platform finished work can go to.
public struct Destination: Codable, Equatable, Sendable, Identifiable, Hashable {
    public var target: String
    public var label: String
    public var threadsSupported: Bool
    public var id: String { target }
    enum CodingKeys: String, CodingKey { case target, label, name, threadsSupported = "threads_supported" }

    public init(target: String, label: String, threadsSupported: Bool = false) {
        self.target = target; self.label = label; self.threadsSupported = threadsSupported
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        target = try c.decode(String.self, forKey: .target)
        label = (try? c.decodeIfPresent(String.self, forKey: .label)) ?? (try? c.decodeIfPresent(String.self, forKey: .name))
            ?? target.capitalized
        threadsSupported = (try? c.decodeIfPresent(Bool.self, forKey: .threadsSupported)) ?? false
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(target, forKey: .target); try c.encode(label, forKey: .label)
        try c.encode(threadsSupported, forKey: .threadsSupported)
    }

    /// Server shape (docs/API.md): `{"destinations": [{"platform", "connected", "target",
    /// "home_channel": {"name", "target"}, "chats": [{"name", "target"}]}], "threads_supported"}`.
    /// Flattens each platform into its home channel and chats. Also accepts plain
    /// `{"target", "label"}` entries and bare strings. Drops "none" and duplicates.
    public static func list(_ data: Data) -> [Destination] {
        let json = try? JSONSerialization.jsonObject(with: data)
        let top = json as? [String: Any]
        let threads = top?["threads_supported"] as? Bool ?? false
        let array = (json as? [Any]) ?? (top?["destinations"] as? [Any]) ?? []
        var out: [Destination] = []
        for item in array {
            if let name = item as? String { out.append(Destination(target: name, label: name.capitalized)); continue }
            guard let object = item as? [String: Any] else { continue }
            if let platform = object["platform"] as? String {
                if object["connected"] as? Bool == false { continue }
                let title = platform.capitalized
                let entryThreads = object["threads_supported"] as? Bool ?? threads
                if let home = object["home_channel"] as? [String: Any], let t = home["target"] as? String {
                    let n = (home["name"] as? String).map { " · \($0)" } ?? ""
                    out.append(Destination(target: t, label: title + n, threadsSupported: entryThreads))
                } else if let t = object["target"] as? String {
                    out.append(Destination(target: t, label: title, threadsSupported: entryThreads))
                }
                for chat in object["chats"] as? [[String: Any]] ?? [] {
                    guard let t = chat["target"] as? String else { continue }
                    out.append(Destination(target: t, label: "\(title) · \(chat["name"] as? String ?? t)",
                                           threadsSupported: entryThreads))
                }
                continue
            }
            guard let d = try? JSONDecoder().decode(Destination.self, from: JSONSerialization.data(withJSONObject: object)) else { continue }
            out.append(d)
        }
        var seen = Set<String>()
        return out.filter { !$0.target.isEmpty && $0.target != "none" && seen.insert($0.target).inserted }
    }
}

/// `GET /voice/onboarding` (which steps the server considers done) and the `POST` body.
public struct OnboardingStatus: Equatable, Sendable {
    public var done: Set<String>
    public init(done: Set<String> = []) { self.done = done }

    /// Server shape (docs/API.md): `{"steps": {"names_set": true, ...}, "complete": false, ...}`.
    /// Also tolerates `{"done": [...]}` and `{"completed": true}`.
    public static func parse(_ data: Data) -> OnboardingStatus {
        guard let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return OnboardingStatus() }
        var done = Set<String>()
        if let steps = object["steps"] as? [String: Any] {
            for (k, v) in steps where (v as? Bool) == true || ((v as? [String: Any])?["done"] as? Bool) == true { done.insert(k) }
        }
        if let list = object["done"] as? [String] { done.formUnion(list) }
        if object["complete"] as? Bool == true || object["completed"] as? Bool == true { done.insert("complete") }
        return OnboardingStatus(done: done)
    }

    public var isComplete: Bool { done.contains("complete") }

    /// `POST /voice/onboarding` accepts only assistant_name, user_name, delivery_target, write_brief.
    /// The per-task-thread choice isn't part of onboarding; it's saved with `PATCH /voice/settings`.
    public static func postBody(assistantName: String, userName: String, target: String?, writeBrief: Bool = true) throws -> Data {
        let name = assistantName.trimmingCharacters(in: .whitespacesAndNewlines)
        let user = userName.trimmingCharacters(in: .whitespacesAndNewlines)
        let body: [String: Any] = [
            "assistant_name": name.isEmpty ? VoiceState.defaultAssistantName : name,
            "user_name": user,
            "delivery_target": target ?? "none",
            "write_brief": writeBrief,
        ]
        return try JSONSerialization.data(withJSONObject: body, options: [.sortedKeys])
    }
}

extension ISO8601DateFormatter {
    static func flexible(_ text: String) -> Date? {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = f.date(from: text) { return d }
        f.formatOptions = [.withInternetDateTime]
        return f.date(from: text)
    }
}
