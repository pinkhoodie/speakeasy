import XCTest
@testable import SpeakeasyCore

/// Replies recorded from the real plugin server (plugin/tests/e2e_local.py with
/// SPEAKEASY_CONTRACT_DIR set). If the server's JSON changes, re-record them; these tests
/// then show exactly where the app stops understanding it.
final class ContractTests: XCTestCase {
    private func fixture(_ name: String) throws -> Data {
        let url = try XCTUnwrap(Bundle.module.url(forResource: name, withExtension: "json", subdirectory: "Contract"))
        return try Data(contentsOf: url)
    }

    func testStatus() throws {
        let s = try JSONDecoder().decode(ServerStatus.self, from: fixture("status"))
        XCTAssertEqual(s.assistantName, "Nova")
        XCTAssertEqual(s.provider, "codex")
        XCTAssertEqual(s.codexSignedIn, true)
        XCTAssertEqual(s.hermesAPIOK, true)
        XCTAssertEqual(s.threadsSupported, true)
        XCTAssertNotNil(s.voiceReady, "the server says whether a call can start")
    }

    func testSettings() throws {
        let s = try ServerSettings.decode(fixture("settings"))
        XCTAssertEqual(s.assistantName, "Nova")
        XCTAssertEqual(s.userName, "Sam")
        XCTAssertNil(s.deliveryTarget, "\"none\" means no delivery")
    }

    func testOnboarding() throws {
        XCTAssertFalse(OnboardingStatus.parse(try fixture("onboarding")).isComplete)
        let after = OnboardingStatus.parse(try fixture("onboarding_post"))
        XCTAssertTrue(after.isComplete)
        XCTAssertTrue(after.done.isSuperset(of: ["paired", "names_set", "delivery_set"]))
    }

    /// The bodies the app sends, written where the plugin's e2e test replays them against the real
    /// server (`app request accepted: ...`). Keeps the request side of the contract honest too.
    func testRecordAppRequests() throws {
        let dir = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .appendingPathComponent("Contract/requests", isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        func record(_ name: String, _ method: String, _ path: String, _ body: Data) throws {
            let object = try JSONSerialization.jsonObject(with: body)
            let spec: [String: Any] = ["method": method, "path": path, "body": object]
            try JSONSerialization.data(withJSONObject: spec, options: [.sortedKeys, .prettyPrinted])
                .write(to: dir.appendingPathComponent(name + ".json"))
        }
        var full = try ServerSettings.decode(fixture("settings"))
        try record("settings_patch", "PATCH", "/voice/settings", full.patchBody())
        full.delivery = .init(target: nil, newThread: false)
        try record("settings_patch_no_delivery", "PATCH", "/voice/settings", full.patchBody())
        full.delivery = .init(target: "telegram:555000111", newThread: false, channels: [
            .init(target: "discord:9000000000001", label: "#general", topic: "everyday questions and errands", newThread: true)])
        try record("settings_patch_channels", "PATCH", "/voice/settings", full.patchBody())
        full.continuity = .init(enabled: false)
        try record("settings_patch_continuity_off", "PATCH", "/voice/settings", full.patchBody())
        try record("onboarding_post", "POST", "/voice/onboarding",
                   OnboardingStatus.postBody(assistantName: "Nova", userName: "Sam", target: nil, writeBrief: false,
                                             continuity: true))
        let brief = """
        ## User
        Sam, a product designer in Lisbon. Call him Sam.
        ## Assistant persona
        Nova: calm, direct, a little dry. First person, no filler.
        ## Capability map
        Can check the calendar, draft email for approval, search the web and message Sam on Telegram.
        ## Answer preferences
        Short spoken answers, the key number first, details on screen.
        ## Current context
        Preparing a client pitch this week.
        """
        try record("brief_put", "PUT", "/voice/brief", VoiceBrief.putBody(text: brief))
        let home = try HomeControlInfo.decode(fixture("home_put"))
        try record("home_put", "PUT", "/voice/home", HomeControlInfo.putBody(enabled: true, entities: Array(home.includedIDs.prefix(2))))
    }

    /// Requests made inside a call ("Look at this"), as the app builds them. Paths name
    /// `{interaction_id}`, `{capture_id}` and `{attachment_id}`; session bodies carry `{sdp}`. Uploads
    /// list their headers and say what raw bytes go in the body (`raw_body`: jpeg | text).
    private func callRequestSpecs() throws -> [String: [String: Any]] {
        let (call, capture, attachment) = ("vi_contract", "cap_contract", "att_contract")
        func placeholders(_ text: String) -> String {
            text.replacingOccurrences(of: call, with: "{interaction_id}").replacingOccurrences(of: capture, with: "{capture_id}")
                .replacingOccurrences(of: attachment, with: "{attachment_id}")
        }
        func spec(_ method: String, _ path: String, body: Data? = nil, headers: [String: String]? = nil,
                  raw: String? = nil) throws -> [String: Any] {
            var spec: [String: Any] = ["method": method, "path": placeholders(path)]
            spec["body"] = try body.map { try JSONSerialization.jsonObject(with: Data(placeholders(String(decoding: $0, as: UTF8.self)).utf8)) }
                ?? NSNull()
            if let headers { spec["headers"] = headers.mapValues(placeholders) }
            if let raw { spec["raw_body"] = raw }
            return spec
        }
        return [
            "session_screen_ready": try spec("POST", "/voice/sessions", body: sessionRequestBody(sdp: "{sdp}", screen: .ready)),
            "session_resume_no_permission": try spec("POST", "/voice/sessions",
                                                     body: sessionRequestBody(sdp: "{sdp}", resumeFrom: call, screen: .noPermission)),
            "screen_on": try spec("POST", SharingRoute.screen(call), body: ScreenToggle(on: true, seq: 1).body()),
            "screen_off": try spec("POST", SharingRoute.screen(call), body: ScreenToggle(on: false, seq: 2).body()),
            "capture_capturing": try spec("POST", SharingRoute.capture(call, capture), body: CaptureReport.capturing.body()),
            "capture_uploading": try spec("POST", SharingRoute.capture(call, capture), body: CaptureReport.uploading.body()),
            "capture_failed": try spec("POST", SharingRoute.capture(call, capture), body: CaptureReport.failed(.secureInput).body()),
            "attachment_screen": try spec("POST", SharingRoute.attachments(call), headers: attachmentUploadHeaders(
                kind: .screen, mimeType: "image/jpeg", filename: "screen.jpg", captureID: capture, app: "Xcode"), raw: "jpeg"),
            "attachment_picture": try spec("POST", SharingRoute.attachments(call), headers: attachmentUploadHeaders(
                kind: .picture, mimeType: "image/jpeg", filename: "Header idea.jpg"), raw: "jpeg"),
            "attachment_file": try spec("POST", SharingRoute.attachments(call), headers: attachmentUploadHeaders(
                kind: .file, mimeType: "application/pdf", filename: "Q3 report.pdf"), raw: "text"),
            "attachment_delete": try spec("DELETE", SharingRoute.attachment(call, attachment)),
        ]
    }

    /// Writes the in-call requests under `Contract/requests/call/`, so the top-level replay above (no
    /// call open) skips them; the plugin's e2e replays them inside a call.
    func testRecordCallRequests() throws {
        let dir = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .appendingPathComponent("Contract/requests/call", isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        for (name, spec) in try callRequestSpecs() {
            try JSONSerialization.data(withJSONObject: spec, options: [.sortedKeys, .prettyPrinted])
                .write(to: dir.appendingPathComponent(name + ".json"))
        }
    }

    /// The recorded in-call requests follow the plugin's rules (plugin/speakeasy/service.py: set_screen,
    /// capture_status, check_upload; tests/test_attachments_route.py).
    func testCallRequestsMatchWhatThePluginAccepts() throws {
        let specs = try callRequestSpecs()
        func spec(_ name: String) throws -> [String: Any] { try XCTUnwrap(specs[name], name) }
        for name in ["session_screen_ready", "session_resume_no_permission"] {
            let body = try XCTUnwrap(try spec(name)["body"] as? [String: Any])
            XCTAssertTrue(["ready", "no_permission"].contains(body["screen"] as? String), name)
            XCTAssertEqual(body["sdp"] as? String, "{sdp}")
        }
        XCTAssertEqual((try spec("session_resume_no_permission")["body"] as? [String: Any])?["resume_from"] as? String, "{interaction_id}")
        for name in ["screen_on", "screen_off"] {
            let s = try spec(name)
            XCTAssertEqual(s["path"] as? String, "/voice/interactions/{interaction_id}/screen")
            let body = try XCTUnwrap(s["body"] as? [String: Any])
            XCTAssertEqual(Set(body.keys), ["on", "seq"], "exactly {on, seq}")
            XCTAssertTrue(body["on"] is Bool && (body["seq"] as? Int).map { $0 >= 0 } == true)
        }
        let reasons = Set(AttachmentFailure.allCases.map(\.rawValue))
        for name in ["capture_capturing", "capture_uploading", "capture_failed"] {
            let s = try spec(name)
            XCTAssertEqual(s["path"] as? String, "/voice/interactions/{interaction_id}/captures/{capture_id}")
            let body = try XCTUnwrap(s["body"] as? [String: String])
            XCTAssertTrue(Set(body.keys).isSubset(of: ["status", "reason"]))
            XCTAssertTrue(["capturing", "uploading", "failed"].contains(body["status"] ?? ""))
            XCTAssertEqual(body["reason"] != nil, body["status"] == "failed", "a reason only with failed")
            if let reason = body["reason"] { XCTAssertTrue(reasons.contains(reason)) }
        }
        XCTAssertEqual(reasons, ["permission", "no_window", "speakeasy_window", "password_manager", "secure_input", "blank",
                                 "too_large", "timeout", "unsupported"], "the plugin's FAILURE_REASONS")
        for (name, kind) in [("attachment_screen", "screen"), ("attachment_picture", "picture"), ("attachment_file", "file")] {
            let s = try spec(name)
            XCTAssertEqual(s["path"] as? String, "/voice/interactions/{interaction_id}/attachments")
            let headers = try XCTUnwrap(s["headers"] as? [String: String])
            XCTAssertEqual(headers["X-Speakeasy-Kind"], kind)
            XCTAssertEqual(headers["X-Speakeasy-Capture-Id"] != nil, kind == "screen", "a capture id with a capture only")
            if kind != "file" { XCTAssertTrue(["image/jpeg", "image/png"].contains(headers["Content-Type"] ?? ""), name) }
            if kind == "file" { XCTAssertFalse((headers["X-Speakeasy-Filename"] ?? "").isEmpty, "files need a name") }
            XCTAssertNil(headers["Content-Length"], "URLSession sets it from the body")
        }
        let delete = try spec("attachment_delete")
        XCTAssertEqual(delete["method"] as? String, "DELETE")
        XCTAssertEqual(delete["path"] as? String, "/voice/interactions/{interaction_id}/attachments/{attachment_id}")
    }

    /// Replies shaped exactly as the plugin builds them (service.attachment_status, Interaction.snapshot,
    /// the routes' replies), until the e2e run records real ones.
    func testLookAtThisReplies() throws {
        let status = try JSONDecoder().decode(ServerStatus.self, from: Data(#"""
        {"assistant_name": "Nova", "version": "0.2.48", "attachments": {"images": true, "vision": "described", "max_attachments": 3,
         "max_image_bytes": 2500000, "max_request_image_bytes": 6500000, "max_file_bytes": 10000000}}
        """#.utf8))
        XCTAssertTrue(status.attachmentsSupported)
        XCTAssertEqual(status.attachments?.maxAttachments, AttachmentPolicy.maxAttachments, "the Mac and the plugin agree")
        XCTAssertEqual(status.attachments?.maxImageBytes, AttachmentPolicy.maxImageBytes)
        XCTAssertEqual(status.attachments?.maxRequestImageBytes, AttachmentPolicy.maxRequestImageBytes)
        XCTAssertEqual(status.attachments?.maxFileBytes, AttachmentPolicy.maxFileBytes)

        let snapshot = try XCTUnwrap(InteractionSnapshot(json: try JSONSerialization.jsonObject(with: Data(#"""
        {"interaction_id": "vi_1", "status": "live", "run_id": null, "backend_run_id": null, "approval": null,
         "finalization": null, "error": null, "paused": false, "resumed_from": null,
         "screen": {"on": true, "seq": 1, "declared": "ready"}, "captures": [{"capture_id": "cap_0123456789abcdef"}],
         "attachments": [{"id": "att_0123456789abcdef", "kind": "file", "name": "Q3 report.pdf", "app": null, "state": "pending"}],
         "hold": null}
        """#.utf8))))
        XCTAssertEqual(snapshot.screen, ScreenSnapshot(on: true, seq: 1, declared: .ready))
        XCTAssertEqual(snapshot.captures, ["cap_0123456789abcdef"])
        XCTAssertEqual(snapshot.attachments, [ServerAttachment(id: "att_0123456789abcdef", kind: "file", name: "Q3 report.pdf")])
        XCTAssertTrue(snapshot.hasHold)
        XCTAssertEqual(ScreenToggle(json: try JSONSerialization.jsonObject(with: Data(#"{"on": false, "seq": 2}"#.utf8))),
                       ScreenToggle(on: false, seq: 2), "the /screen reply")
    }

    func testHomeControl() throws {
        let found = try HomeControlInfo.decode(fixture("home"))
        XCTAssertTrue(found.available)
        XCTAssertFalse(found.enabled, "off until the user turns it on")
        XCTAssertFalse(found.devices.isEmpty)
        XCTAssertFalse(found.explainer.isEmpty)
        XCTAssertEqual(found.groups.first?.kind, "light", "lights first")
        let on = try HomeControlInfo.decode(fixture("home_put"))
        XCTAssertTrue(on.enabled)
        XCTAssertTrue(on.configured)
        XCTAssertFalse(on.includedIDs.isEmpty, "turning on saves the default picks")
        XCTAssertFalse(on.devices.contains { $0.kind == "lock" && $0.included }, "locks are never ticked by default")
    }

    func testDestinationsAndSuggestion() throws {
        let raw = try fixture("destinations")
        let list = Destination.list(raw)
        XCTAssertEqual(list.map(\.target), ["discord:9000000000001", "discord:9000000000002", "telegram:555000111"])
        XCTAssertEqual(list.first?.label, "Discord · Home / general")
        XCTAssertEqual(Destination.suggested(raw), "telegram:555000111")
    }

    func testBriefRoundTrip() throws {
        let b = try JSONDecoder().decode(VoiceBrief.self, from: fixture("brief"))
        XCTAssertEqual(b.state, "none")
        XCTAssertEqual(b.text, "")
        let body = try JSONSerialization.jsonObject(with: VoiceBrief.putBody(text: "About Sam")) as? [String: Any]
        XCTAssertEqual(Set(body?.keys ?? [:].keys), ["brief"], "PUT /voice/brief takes exactly {\"brief\": ...}")
        let withText = try JSONDecoder().decode(VoiceBrief.self, from: Data(#"{"brief":"About Sam","state":"edited","edited":true}"#.utf8))
        XCTAssertEqual(withText.text, "About Sam")
    }
}
