import Foundation

/// An email a task drafted and is waiting to send (`email_drafts` on a task/work row).
/// Actions go to `POST /voice/drafts/{draft_id}` with the on-screen `sha256`.
public struct EmailDraft: Equatable, Sendable, Identifiable {
    public enum Status: String, Sendable, CaseIterable {
        case pending, approved, denied, revising, sent, failed, superseded
    }

    public var draftID: String
    public var sha256: String
    public var from: String
    public var to: [String]
    public var cc: [String]
    public var bcc: [String]
    public var subject: String
    public var body: String
    public var status: Status
    public var id: String { draftID }

    public init(draftID: String, sha256: String, from: String = "", to: [String] = [], cc: [String] = [],
                bcc: [String] = [], subject: String = "", body: String = "", status: Status = .pending) {
        self.draftID = draftID; self.sha256 = sha256; self.from = from; self.to = to; self.cc = cc
        self.bcc = bcc; self.subject = subject; self.body = body; self.status = status
    }

    /// Needs draft_id and sha256; an unknown status is treated as pending (never auto-sent,
    /// but still shown so the user can decide). Address lists accept an array or a single string.
    public init?(json: Any?) {
        guard let o = json as? [String: Any],
              let id = (o["draft_id"] as? String).flatMap({ $0.isEmpty ? nil : $0 }),
              let sha = (o["sha256"] as? String).flatMap({ $0.isEmpty ? nil : $0 }) else { return nil }
        func list(_ key: String) -> [String] {
            if let s = o[key] as? String { return s.isEmpty ? [] : [s] }
            return (o[key] as? [Any] ?? []).compactMap { $0 as? String }.filter { !$0.isEmpty }
        }
        self.init(draftID: id, sha256: sha, from: o["from"] as? String ?? "", to: list("to"), cc: list("cc"),
                  bcc: list("bcc"), subject: o["subject"] as? String ?? "", body: o["body"] as? String ?? "",
                  status: (o["status"] as? String).flatMap(Status.init(rawValue:)) ?? .pending)
    }

    public static func list(json: Any?) -> [EmailDraft] {
        (json as? [Any] ?? []).compactMap(EmailDraft.init(json:))
    }

    /// Show the full card with actions.
    public var isActionable: Bool { status == .pending }
    /// A newer draft replaced this one: collapse it.
    public var isCollapsed: Bool { status == .superseded }

    /// Short state line shown after an action (nil while pending).
    public var stateLabel: String? {
        switch status {
        case .pending: return nil
        case .approved: return "Sending…"
        case .sent: return "Sent"
        case .denied: return "Discarded"
        case .revising: return "Revising…"
        case .failed: return "Couldn't send"
        case .superseded: return "Replaced by a newer draft"
        }
    }
}

public enum DraftAction: String, Sendable {
    case approve, deny, revise

    /// Status shown optimistically right after the tap (the server confirms later).
    public var optimisticStatus: EmailDraft.Status {
        switch self {
        case .approve: return .approved
        case .deny: return .denied
        case .revise: return .revising
        }
    }
}

/// Body for `POST /voice/drafts/{draft_id}`. Always carries the hash of the draft the user saw.
public func draftActionBody(_ action: DraftAction, draft: EmailDraft, instructions: String? = nil) throws -> Data {
    var object: [String: Any] = ["action": action.rawValue, "sha256": draft.sha256]
    if action == .revise {
        let text = instructions?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        guard !text.isEmpty else { throw DraftActionError.emptyInstructions }
        object["instructions"] = text
    }
    return try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
}

public enum DraftActionError: Error, Equatable { case emptyInstructions, changed }

/// The message shown after a 409 (the draft changed under the user).
public let draftChangedMessage = "The draft changed — review it again"

extension VoiceState {
    /// Every pending email draft across this call's tasks (and the current work), newest task last.
    public var pendingDrafts: [EmailDraft] {
        var seen = Set<String>()
        let all = tasks.flatMap(\.info.emailDrafts) + (workInfo?.emailDrafts ?? [])
        return all.filter { $0.isActionable && seen.insert($0.draftID).inserted }
    }
}

extension WorkInfo {
    /// This task's drafts still waiting for Send / Deny / Revise.
    public var pendingDrafts: [EmailDraft] { emailDrafts.filter(\.isActionable) }
}
