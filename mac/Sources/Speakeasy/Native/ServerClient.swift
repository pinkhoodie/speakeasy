import Foundation
import SpeakeasyCore

/// Authenticated api HTTP client. The device bearer is attached only to URLs
/// under the trusted api origin re-validated at request time.
final class ServerClient: @unchecked Sendable {
    struct HTTPError: LocalizedError {
        let status: Int
        let message: String
        var errorDescription: String? { message }
    }

    let base: URL
    private let token: String
    private let session: URLSession

    init?(config: AppConfig) {
        guard let token = config.deviceToken, let server = config.serverURL,
              let trusted = trustedServerBaseURL(server.absoluteString) else { return nil }
        self.base = trusted
        self.token = token
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 30
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.httpCookieStorage = nil
        configuration.urlCache = nil
        session = URLSession(configuration: configuration)
    }

    func url(_ path: String) -> URL {
        var text = base.absoluteString
        while text.hasSuffix("/") { text.removeLast() }
        return URL(string: text + path)!
    }

    func request(_ path: String, method: String = "GET", body: Data? = nil,
                 headers: [String: String] = [:], timeout: TimeInterval = 30) -> URLRequest {
        var request = URLRequest(url: url(path), timeoutInterval: timeout)
        request.httpMethod = method
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        for (key, value) in headers { request.setValue(value, forHTTPHeaderField: key) }
        request.httpBody = body
        return request
    }

    /// Raw response for the Speakeasy settings routes (typed decoding lives in the core).
    func data(_ path: String, method: String = "GET", body: Data? = nil) async throws -> Data {
        let (data, response) = try await session.data(for: request(path, method: method, body: body))
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else {
            let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            throw HTTPError(status: status, message: object?["error"] as? String ?? "Server HTTP \(status)")
        }
        return data
    }

    // MARK: Speakeasy server routes

    func settings() async throws -> ServerSettings { try ServerSettings.decode(try await data("/voice/settings")) }
    func saveSettings(_ settings: ServerSettings) async throws -> ServerSettings {
        let out = try await data("/voice/settings", method: "PATCH", body: try settings.patchBody())
        return (try? ServerSettings.decode(out)) ?? settings
    }
    func status() async throws -> ServerStatus { try JSONDecoder().decode(ServerStatus.self, from: try await data("/voice/status")) }
    func brief() async throws -> VoiceBrief { try JSONDecoder().decode(VoiceBrief.self, from: try await data("/voice/brief")) }
    func saveBrief(_ text: String) async throws -> VoiceBrief? {
        try? JSONDecoder().decode(VoiceBrief.self, from: try await data("/voice/brief", method: "PUT", body: try VoiceBrief.putBody(text: text)))
    }
    func rewriteBrief() async throws -> VoiceBrief? {
        try? JSONDecoder().decode(VoiceBrief.self, from: try await data("/voice/brief/rewrite", method: "POST", body: Data("{}".utf8)))
    }
    func destinations() async throws -> [Destination] { Destination.list(try await data("/voice/destinations")) }
    func onboarding() async throws -> OnboardingStatus { OnboardingStatus.parse(try await data("/voice/onboarding")) }
    func completeOnboarding(assistantName: String, userName: String, target: String?) async throws {
        _ = try await data("/voice/onboarding", method: "POST", body: try OnboardingStatus.postBody(
            assistantName: assistantName, userName: userName, target: target))
    }

    /// Approve / deny / revise an email draft. Throws `DraftActionError.changed` on 409.
    func draftAction(_ action: DraftAction, draft: EmailDraft, instructions: String?) async throws {
        do {
            _ = try await data("/voice/drafts/\(escape(draft.draftID))", method: "POST",
                               body: try draftActionBody(action, draft: draft, instructions: instructions))
        } catch let error as HTTPError where error.status == 409 {
            throw DraftActionError.changed
        }
    }

    /// `POST /voice/pair` (no auth): trade a one-time code for a device token.
    static func pair(server: URL, code: String, deviceName: String) async throws -> PairResponse {
        guard let base = trustedServerBaseURL(server.absoluteString) else {
            throw HTTPError(status: 0, message: "That server address isn't allowed. Use this Mac (127.0.0.1) or an https Tailscale address.")
        }
        var request = URLRequest(url: base.appendingPathComponent("voice/pair"), timeoutInterval: 20)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try pairRequestBody(code: code, deviceName: deviceName)
        let (data, response) = try await URLSession(configuration: .ephemeral).data(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else {
            let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            let fallback = status == 403 || status == 400 ? "That code is wrong or expired. Run hermes voice pair for a new one."
                : "Pairing failed (HTTP \(status))"
            throw HTTPError(status: status, message: object?["error"] as? String ?? fallback)
        }
        return try JSONDecoder().decode(PairResponse.self, from: data)
    }

    func json(_ path: String, method: String = "GET", body: Data? = nil,
              headers: [String: String] = [:]) async throws -> [String: Any] {
        let (data, response) = try await session.data(for: request(path, method: method, body: body, headers: headers))
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        guard (200..<300).contains(status) else {
            throw HTTPError(status: status, message: object["error"] as? String ?? "Server HTTP \(status)")
        }
        return object
    }

    func post(_ path: String, _ object: [String: Any], headers: [String: String] = [:]) async throws -> [String: Any] {
        try await json(path, method: "POST", body: try JSONSerialization.data(withJSONObject: object), headers: headers)
    }

    /// POST the SDP offer byte-for-byte (the body encoder never trims it).
    func admitSession(sdp: String, idempotencyKey: String, resumeFrom: String? = nil) async throws -> SessionAdmission {
        let body = try sessionRequestBody(sdp: sdp, resumeFrom: resumeFrom)
        let object = try await json("/voice/sessions", method: "POST", body: body,
                                    headers: ["Idempotency-Key": idempotencyKey])
        guard let admission = SessionAdmission(json: object) else {
            throw HTTPError(status: 502, message: "Server returned an invalid session answer")
        }
        return admission
    }

    func interaction(_ id: String) async throws -> InteractionSnapshot? {
        InteractionSnapshot(json: try await json("/voice/interactions/\(escape(id))"))
    }

    /// The Codex data channel closes before its app-server thread stops.
    func finishCodexTransport(interactionID: String) async throws {
        _ = try await post("/voice/interactions/\(escape(interactionID))/end", [:])
    }

    func work(runID: String?) async throws -> WorkInfo? {
        let object = try await json(runID.map { "/voice/work/\(escape($0))" } ?? "/voice/work/latest")
        return WorkInfo(json: object["work"])
    }

    func image(runID: String, index: Int) async throws -> Data {
        guard (1...8).contains(index) else { throw HTTPError(status: 400, message: "Invalid card") }
        let (data, response) = try await session.data(for: request("/voice/card-image/\(escape(runID))/\(index)"))
        guard let http = response as? HTTPURLResponse, http.statusCode == 200,
              http.mimeType?.hasPrefix("image/") == true, !data.isEmpty, data.count <= 8_000_000 else {
            throw HTTPError(status: (response as? HTTPURLResponse)?.statusCode ?? 502, message: "Image unavailable")
        }
        return data
    }

    /// Latest work plus every task of the most recent call (after-call Work view).
    func latestWork() async throws -> (WorkInfo?, [TaskItem]) {
        let object = try await json("/voice/work/latest")
        return (WorkInfo(json: object["work"]), TaskItem.list(json: object["tasks"]) ?? [])
    }

    /// Clear finished tasks from the task list (running tasks are never cleared).
    func dismissTasks(runIDs: [String]) async throws {
        _ = try await post("/voice/tasks/dismiss", ["run_ids": runIDs])
    }

    /// Hold the call's conversation and tasks for Resume before the session closes.
    func pause(interactionID: String) async throws {
        _ = try await json("/voice/interactions/\(escape(interactionID))/pause", method: "POST", body: Data("{}".utf8))
    }

    func cancelBackend(interactionID: String, runID: String) async throws {
        _ = try await post("/voice/interactions/\(escape(interactionID))/cancel-backend", ["run_id": runID])
    }

    func resolveApproval(interactionID: String, approval: ApprovalInfo, choice: String) async throws {
        precondition(choice == "once" || choice == "deny")
        _ = try await post("/voice/interactions/\(escape(interactionID))/approval",
                           ["run_id": approval.runID, "request_id": approval.requestID, "choice": choice])
    }

    private func escape(_ id: String) -> String {
        id.addingPercentEncoding(withAllowedCharacters: .alphanumerics.union(CharacterSet(charactersIn: "-_"))) ?? id
    }

    // MARK: - Event stream

    enum StreamOutcome { case ended, unsupported, failed(Error) }

    /// Reads `GET /voice/interactions/<id>/events` until the server ends it.
    /// Returns `.unsupported` on 404 so the caller falls back to polling.
    func streamEvents(interactionID: String, parser: inout SSEParser,
                      onEvent: (SSEEvent) async -> Void) async -> StreamOutcome {
        var headers = ["Accept": "text/event-stream"]
        if let last = parser.lastEventID { headers["Last-Event-ID"] = last }
        var req = request("/voice/interactions/\(escape(interactionID))/events", headers: headers, timeout: 3600)
        req.timeoutInterval = 3600
        do {
            let (bytes, response) = try await session.bytes(for: req)
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            if status == 404 || status == 405 || status == 501 { return .unsupported }
            guard (200..<300).contains(status) else { return .failed(HTTPError(status: status, message: "Event stream HTTP \(status)")) }
            var line = Data()
            for try await byte in bytes {
                line.append(byte)
                if byte == 0x0A {
                    let events = parser.append(String(decoding: line, as: UTF8.self))
                    line.removeAll(keepingCapacity: true)
                    for event in events { await onEvent(event) }
                }
                if Task.isCancelled { return .ended }
            }
            return .ended
        } catch {
            if Task.isCancelled { return .ended }
            return .failed(error)
        }
    }
}
