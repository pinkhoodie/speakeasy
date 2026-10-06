import SwiftUI
import SpeakeasyCore

/// What a question card can do in this host: send the picked answer back to its task. Hosts that
/// can't answer (or a call that ended) leave `onAnswer` nil and the card says to answer by voice.
public struct QuestionActions {
    public var onAnswer: ((String) -> Void)?
    public var answered: String?
    /// Loads a task picture by card number (the authenticated card-image route); nil = no pictures.
    public var image: ((Int) async -> Data?)?
    public init(onAnswer: ((String) -> Void)? = nil, answered: String? = nil, image: ((Int) async -> Data?)? = nil) {
        self.onAnswer = onAnswer; self.answered = answered; self.image = image
    }
}

private struct QuestionActionsKey: EnvironmentKey { static let defaultValue = QuestionActions() }
public extension EnvironmentValues {
    var questionActions: QuestionActions {
        get { self[QuestionActionsKey.self] }
        set { self[QuestionActionsKey.self] = newValue }
    }
}

/// A decision a task needs: the question, numbered answers (one marked Recommended), answered with
/// one tap, the number key, or out loud. After answering it settles to a quiet "Answered" line.
struct QuestionCard: View {
    let card: ViewCard
    @Environment(\.questionActions) private var actions
    @State private var picked: String?

    var body: some View {
        let options = card["options"].array.compactMap(\.string)
        let recommended = card["recommended"].double.map { Int($0) }
        let answer = picked ?? actions.answered
        CardBox(tint: answer == nil ? .orange : nil) {
            HStack(spacing: 6) {
                Circle().fill(answer == nil ? Color.orange : Color.green).frame(width: 7, height: 7)
                Text(answer == nil ? "Needs you" : "Answered")
                    .font(.system(size: 10.5, weight: .semibold)).foregroundStyle(.secondary)
                Spacer(minLength: 0)
            }
            Text(card["question"].string ?? "").font(.system(size: 14, weight: .semibold))
                .fixedSize(horizontal: false, vertical: true)
            let pictures = card["images"].doubles.map { Int($0) }
            if pictures.contains(where: { $0 > 0 }), let load = actions.image {
                HStack(alignment: .top, spacing: 8) {
                    ForEach(Array(options.prefix(4).enumerated()), id: \.offset) { i, option in
                        PictureOption(number: i + 1, text: option, picture: i < pictures.count ? pictures[i] : 0,
                                      load: load, recommended: i == recommended, chosen: answer == option,
                                      dimmed: answer != nil && answer != option,
                                      enabled: answer == nil && actions.onAnswer != nil) {
                            picked = option
                            actions.onAnswer?(option)
                        }
                    }
                }
            } else {
            VStack(spacing: 6) {
                ForEach(Array(options.prefix(4).enumerated()), id: \.offset) { i, option in
                    OptionRow(number: i + 1, text: option, recommended: i == recommended,
                              chosen: answer == option, dimmed: answer != nil && answer != option,
                              enabled: answer == nil && actions.onAnswer != nil) {
                        picked = option
                        actions.onAnswer?(option)
                    }
                }
            }
            }
            Text(answer != nil ? "Sent to the task." :
                    actions.onAnswer != nil ? "Tap, press 1–\(min(options.count, 4)), or just say it." : "Say your answer.")
                .font(.system(size: 10.5)).foregroundStyle(.tertiary)
        }
        .animation(.spring(response: 0.3, dampingFraction: 0.85), value: answer)
    }
}

private struct OptionRow: View {
    let number: Int
    let text: String
    let recommended: Bool
    let chosen: Bool
    let dimmed: Bool
    let enabled: Bool
    let action: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Text("\(number)")
                    .font(.system(size: 11, weight: .bold, design: .rounded))
                    .frame(width: 20, height: 20)
                    .background(RoundedRectangle(cornerRadius: 6, style: .continuous)
                        .fill(chosen ? Color.accentColor : Color.primary.opacity(0.08)))
                    .foregroundStyle(chosen ? Color.white : Color.secondary)
                Text(text).font(.system(size: 12.5, weight: chosen ? .semibold : .regular)).lineLimit(2)
                Spacer(minLength: 4)
                if recommended && !chosen && !dimmed {
                    Text("Recommended").font(.system(size: 9.5, weight: .semibold))
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .background(Capsule().fill(Color.accentColor.opacity(0.14)))
                        .foregroundStyle(Color.accentColor)
                }
                if chosen { Image(systemName: "checkmark").font(.system(size: 11, weight: .bold)).foregroundStyle(Color.accentColor) }
            }
            .padding(.horizontal, 10).padding(.vertical, 8)
            .contentShape(Rectangle())
            .background(RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(chosen ? Color.accentColor.opacity(0.12) : Color.primary.opacity(hover && enabled ? 0.08 : 0.04)))
            .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous)
                .strokeBorder(recommended && !dimmed && !chosen ? Color.accentColor.opacity(0.35) : Color.clear, lineWidth: 1))
            .opacity(dimmed ? 0.45 : 1)
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        #if os(macOS)
        .onHover { hover = $0 }
        .keyboardShortcut(KeyEquivalent(Character("\(number)")), modifiers: [])
        #endif
    }
}

/// An option that is something to look at: its picture, number and name; click to pick, or press
/// its number. Opens larger on hover via the tooltip-sized preview is left to the task's image cards.
private struct PictureOption: View {
    let number: Int
    let text: String
    let picture: Int
    let load: (Int) async -> Data?
    let recommended: Bool
    let chosen: Bool
    let dimmed: Bool
    let enabled: Bool
    let action: () -> Void
    @State private var image: Image?
    @State private var hover = false

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 6) {
                ZStack(alignment: .topLeading) {
                    RoundedRectangle(cornerRadius: 9, style: .continuous).fill(Color.primary.opacity(0.06))
                    if let image {
                        image.resizable().scaledToFit()   // the whole design, never cropped
                            .frame(minWidth: 0, maxWidth: .infinity, minHeight: 0, maxHeight: .infinity)
                    } else if picture > 0 {
                        ProgressView().controlSize(.small).frame(maxWidth: .infinity, maxHeight: .infinity)
                    }
                    Text("\(number)")
                        .font(.system(size: 10.5, weight: .bold, design: .rounded))
                        .frame(width: 18, height: 18)
                        .background(RoundedRectangle(cornerRadius: 5, style: .continuous)
                            .fill(chosen ? Color.accentColor : Color.black.opacity(0.55)))
                        .foregroundStyle(.white)
                        .padding(5)
                }
                .aspectRatio(4.0 / 3.0, contentMode: .fit)
                .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 9, style: .continuous).strokeBorder(
                    chosen ? Color.accentColor : recommended && !dimmed ? Color.accentColor.opacity(0.5)
                        : Color.primary.opacity(hover && enabled ? 0.25 : 0.08),
                    lineWidth: chosen ? 2 : 1))
                HStack(spacing: 4) {
                    Text(text).font(.system(size: 11.5, weight: chosen ? .semibold : .regular)).lineLimit(2)
                    Spacer(minLength: 0)
                    if chosen { Image(systemName: "checkmark").font(.system(size: 10, weight: .bold)).foregroundStyle(Color.accentColor) }
                }
                if recommended && !chosen && !dimmed {
                    Text("Recommended").font(.system(size: 9.5, weight: .semibold)).foregroundStyle(Color.accentColor)
                }
            }
            .contentShape(Rectangle())
            .opacity(dimmed ? 0.45 : 1)
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .frame(maxWidth: .infinity)
        .task(id: picture) {
            guard picture > 0, image == nil, let data = await load(picture) else { return }
            #if os(macOS)
            if let ns = NSImage(data: data) { image = Image(nsImage: ns) }
            #else
            if let ui = UIImage(data: data) { image = Image(uiImage: ui) }
            #endif
        }
        #if os(macOS)
        .onHover { hover = $0 }
        .keyboardShortcut(KeyEquivalent(Character("\(number)")), modifiers: [])
        #endif
    }
}
