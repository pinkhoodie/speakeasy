import AVFoundation
import Foundation

/// Plays the short bundled voice samples (`Contents/Resources/VoiceSamples/<voice>.m4a`) locally.
/// Nothing is sent anywhere and no call is started.
@MainActor
final class VoicePreviewPlayer: NSObject, ObservableObject, AVAudioPlayerDelegate {
    @Published private(set) var playing: String?
    private var player: AVAudioPlayer?

    static func url(for voice: String) -> URL? {
        Bundle.main.url(forResource: voice, withExtension: "m4a", subdirectory: "VoiceSamples")
    }

    func toggle(_ voice: String) {
        if playing == voice { stop(); return }
        stop()
        guard let url = Self.url(for: voice), let next = try? AVAudioPlayer(contentsOf: url) else { return }
        next.delegate = self
        player = next
        playing = voice
        next.play()
    }

    func stop() {
        player?.stop()
        player = nil
        playing = nil
    }

    nonisolated func audioPlayerDidFinishPlaying(_ finished: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in if self.player === finished { self.stop() } }
    }
}
