import Foundation
import SpeakeasyCore
import SpeakeasyClient

/// `--live-call-smoke`: one real call against a real voice server, no microphone and no UI.
/// Reads the server address and a device token from the environment (never from arguments, so the
/// token stays out of process listings): SPEAKEASY_SMOKE_SERVER, SPEAKEASY_SMOKE_TOKEN_FILE.
/// Opens the call through the same engine and admission path the app uses, sends one typed line,
/// waits for the spoken reply's transcript, then ends the call and checks the server saw it end.
@MainActor
enum LiveCallSmoke {
    static func run() {
        setvbuf(stdout, nil, _IOLBF, 0)
        let env = ProcessInfo.processInfo.environment
        guard let server = env["SPEAKEASY_SMOKE_SERVER"].flatMap(URL.init(string:)),
              let tokenPath = env["SPEAKEASY_SMOKE_TOKEN_FILE"],
              let token = try? String(contentsOfFile: tokenPath, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines),
              !token.isEmpty,
              let api = ServerClient(config: AppConfig(serverURL: server, deviceToken: token)) else {
            print("live-call-smoke failed: set SPEAKEASY_SMOKE_SERVER and SPEAKEASY_SMOKE_TOKEN_FILE"); exit(2)
        }
        let prompt = env["SPEAKEASY_SMOKE_PROMPT"] ?? "Quick sound check. Reply with one short friendly sentence."
        let engine = NativeCallEngine()
        var heard = ""
        var gotReply = false
        var events: [String] = []
        engine.onMessage = { data in
            if let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let type = object["type"] as? String, events.count < 400 { events.append(type) }
            if let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any], (object["type"] as? String) == "error" {
                let e = object["error"] as? [String: Any]
                print("error event: \((e?["message"] as? String ?? String(describing: object)).prefix(300))")
            }
            for event in parseDataChannelMessage(data) {
                switch event {
                case .outputDelta(let text): heard += text
                default: break
                }
            }
            if let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let type = object["type"] as? String,
               type == "output_transcript.added" || type == "response.done" || type == "response.output_audio_transcript.done" {
                if let text = object["text"] as? String ?? object["transcript"] as? String, heard.isEmpty { heard = text }
                if !heard.isEmpty { gotReply = true }
            }
        }
        var opened = false
        engine.onChannelOpen = { opened = true }
        Task { @MainActor in
            let started = Date()
            do {
                try engine.prepare(captureAudio: env["SPEAKEASY_SMOKE_SAY"] != nil)
                let sdp = try await engine.createOffer()
                print("offer built")
                let admission = try await api.admitSession(sdp: sdp, idempotencyKey: UUID().uuidString)
                print("offer built, asking server")
                print("admitted provider=\(admission.voiceProvider)")
                try await engine.setRemoteAnswer(admission.answerSDP)
                // Wait for the data channel, then speak by text.
                let openBy = Date().addingTimeInterval(20)
                while !opened && Date() < openBy { try await Task.sleep(nanoseconds: 100_000_000) }
                print("data channel open=\(opened) after=\(String(format: "%.1f", Date().timeIntervalSince(started)))s")
                guard opened else { throw SmokeError("data channel never opened") }
                var sent = true
                if let line = env["SPEAKEASY_SMOKE_SAY"] {
                    try await Task.sleep(nanoseconds: 1_500_000_000)
                    let say = Process()
                    say.executableURL = URL(fileURLWithPath: "/usr/bin/say")
                    say.arguments = [line]
                    try say.run()
                    print("spoke test line out loud")
                    let replyBy = Date().addingTimeInterval(40)
                    while !gotReply && Date() < replyBy { try await Task.sleep(nanoseconds: 200_000_000) }
                } else
                if let raw = env["SPEAKEASY_SMOKE_EVENTS"], let data = raw.replacingOccurrences(of: "{PROMPT}", with: prompt).data(using: .utf8),
                   let list = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] {
                    for event in list { sent = engine.send(json: event) && sent }
                } else {
                    sent = engine.send(json: ["type": "response.item.create",
                                              "item": ["type": "message", "role": "user",
                                                       "content": [["type": "input_text", "text": prompt]]]])
                }
                print("text sent=\(sent)")
                let replyBy = Date().addingTimeInterval(45)
                while !gotReply && Date() < replyBy { try await Task.sleep(nanoseconds: 200_000_000) }
                let words = heard.split(separator: " ").count
                print("reply received=\(gotReply) words=\(words) after=\(String(format: "%.1f", Date().timeIntervalSince(started)))s")
                if !heard.isEmpty { print("reply text: \(heard.prefix(300))") }
                if !gotReply { print("events seen: \(Array(Set(events)).sorted().joined(separator: ","))") }
                _ = engine.send(json: ["type": "session.close"])
                try? await Task.sleep(nanoseconds: 1_500_000_000)
                engine.close()
                if admission.voiceProvider == "codex" { try? await api.finishCodexTransport(interactionID: admission.interactionID) }
                try? await Task.sleep(nanoseconds: 1_500_000_000)
                let snapshot = try? await api.interaction(admission.interactionID)
                print("server call state=\(snapshot?.status ?? "unknown")")
                print(gotReply ? "live-call-smoke ok" : "live-call-smoke failed: no reply")
                exit(gotReply ? 0 : 1)
            } catch {
                engine.close()
                print("live-call-smoke failed: \(error.localizedDescription)")
                exit(1)
            }
        }
    }

    struct SmokeError: LocalizedError {
        let message: String
        init(_ message: String) { self.message = message }
        var errorDescription: String? { message }
    }
}
