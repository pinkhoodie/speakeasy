import AVFoundation
import Foundation
import WebRTC

/// Native WebRTC transport: one peer connection, one captured audio track,
/// the `oai-events` data channel, and remote audio playout via the default ADM.
/// All public callbacks are delivered on the main queue.
final class NativeCallEngine: NSObject, RTCPeerConnectionDelegate, RTCDataChannelDelegate {
    enum EngineError: LocalizedError {
        case peerConnection, offer(String), iceTimeout, noLocalDescription, remote(String)
        var errorDescription: String? {
            switch self {
            case .peerConnection: return "Could not create the voice connection"
            case .offer(let m): return "Could not create the voice offer: \(m)"
            case .iceTimeout: return "ICE gathering timed out"
            case .noLocalDescription: return "No local voice description"
            case .remote(let m): return "Voice answer rejected: \(m)"
            }
        }
    }

    /// One factory per call. The factory owns WebRTC's audio device module, which
    /// picks the Mac's default input/output (and their formats) when it is created
    /// and never follows a later change. A process-wide factory therefore kept a
    /// call on the MacBook speakers after AirPods connected, and a later call could
    /// play through a stale format (deep, slow "chopped" voice).
    private static let sslReady: Void = { RTCInitializeSSL() }()
    /// Created on first use (in `prepare`), after `warmAudioRoute()` has put a Bluetooth
    /// headset in call mode, so WebRTC's audio device opens at the final rate.
    private lazy var factory: RTCPeerConnectionFactory = {
        _ = NativeCallEngine.sslReady
        return RTCPeerConnectionFactory(encoderFactory: RTCDefaultVideoEncoderFactory(),
                                        decoderFactory: RTCDefaultVideoDecoderFactory())
    }()

    private let routeWarmer = AudioRouteWarmer()
    /// Called on main if the output rate changes mid-call (WebRTC can't follow it).
    var onAudioFormatChanged: (() -> Void)?
    private var answered = false
    private var formatChangedEarly = false

    /// AirPods (any Bluetooth headset) switch from 48 kHz music mode to 24 kHz call mode
    /// when their mic opens. WebRTC sets playout up at the rate it sees and can't adapt,
    /// so the reply plays at half speed (deep, slow). Settle the headset first.
    func warmAudioRoute() async { await routeWarmer.warm() }

    private func watchAudioFormat() {
        routeWarmer.watchOutputRate { [weak self] in
            guard let self, !self.closed else { return }
            if self.answered { self.onAudioFormatChanged?() } else { self.formatChangedEarly = true }
        }
    }

    var onMessage: ((Data) -> Void)?
    var onChannelOpen: (() -> Void)?
    var onChannelClosed: (() -> Void)?
    var onConnectionFailed: (() -> Void)?

    private var peer: RTCPeerConnection?
    private var channel: RTCDataChannel?
    private var localTrack: RTCAudioTrack?
    private var iceComplete: (() -> Void)?
    private var remoteMuted = false
    private var closed = false

    /// Build the peer connection, capture track (AEC/NS on), and data channel.
    /// `captureAudio=false` negotiates an audio transceiver without opening the mic.
    func prepare(captureAudio: Bool = true) throws {
        let configuration = RTCConfiguration()
        configuration.sdpSemantics = .unifiedPlan
        configuration.iceServers = []
        configuration.bundlePolicy = .maxBundle
        configuration.rtcpMuxPolicy = .require
        let constraints = RTCMediaConstraints(mandatoryConstraints: nil, optionalConstraints: nil)
        guard let peer = factory.peerConnection(with: configuration, constraints: constraints, delegate: self) else {
            throw EngineError.peerConnection
        }
        self.peer = peer
        if captureAudio {
            let audioConstraints = RTCMediaConstraints(mandatoryConstraints: nil, optionalConstraints: [
                "googEchoCancellation": kRTCMediaConstraintsValueTrue,
                "googNoiseSuppression": kRTCMediaConstraintsValueTrue,
                "googAutoGainControl": kRTCMediaConstraintsValueTrue,
                "googHighpassFilter": kRTCMediaConstraintsValueTrue,
            ])
            let source = factory.audioSource(with: audioConstraints)
            let track = factory.audioTrack(with: source, trackId: "speakeasy-mic")
            peer.add(track, streamIds: ["speakeasy"])
            localTrack = track
        } else {
            let init_ = RTCRtpTransceiverInit()
            init_.direction = .sendRecv
            peer.addTransceiver(of: .audio, init: init_)
        }
        let channelConfig = RTCDataChannelConfiguration()
        channelConfig.isOrdered = true
        guard let channel = peer.dataChannel(forLabel: "oai-events", configuration: channelConfig) else {
            throw EngineError.peerConnection
        }
        channel.delegate = self
        self.channel = channel
        watchAudioFormat()
    }

    /// Create the offer, set it locally, and wait for ICE gathering to finish so
    /// the returned SDP is complete (non-trickle).
    func createOffer(iceTimeout: TimeInterval = 10) async throws -> String {
        guard let peer else { throw EngineError.peerConnection }
        let constraints = RTCMediaConstraints(mandatoryConstraints: [
            kRTCMediaConstraintsOfferToReceiveAudio: kRTCMediaConstraintsValueTrue,
        ], optionalConstraints: nil)
        let offer: RTCSessionDescription = try await withCheckedThrowingContinuation { continuation in
            peer.offer(for: constraints) { sdp, error in
                if let sdp { continuation.resume(returning: sdp) }
                else { continuation.resume(throwing: EngineError.offer(error?.localizedDescription ?? "unknown")) }
            }
        }
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            peer.setLocalDescription(offer) { error in
                if let error { continuation.resume(throwing: EngineError.offer(error.localizedDescription)) }
                else { continuation.resume() }
            }
        }
        if peer.iceGatheringState != .complete {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                let lock = NSLock()
                var resumed = false
                let finish: (Result<Void, Error>) -> Void = { result in
                    lock.lock(); defer { lock.unlock() }
                    guard !resumed else { return }
                    resumed = true
                    continuation.resume(with: result)
                }
                lock.lock()
                iceComplete = { finish(.success(())) }
                lock.unlock()
                if peer.iceGatheringState == .complete { finish(.success(())) }
                DispatchQueue.global().asyncAfter(deadline: .now() + iceTimeout) { finish(.failure(EngineError.iceTimeout)) }
            }
        }
        guard let sdp = peer.localDescription?.sdp else { throw EngineError.noLocalDescription }
        return sdp
    }

    func setRemoteAnswer(_ sdp: String) async throws {
        guard let peer else { throw EngineError.peerConnection }
        let answer = RTCSessionDescription(type: .answer, sdp: sdp)
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            peer.setRemoteDescription(answer) { error in
                if let error { continuation.resume(throwing: EngineError.remote(error.localizedDescription)) }
                else { continuation.resume() }
            }
        }
        applyRemoteMute()
        answered = true
        if formatChangedEarly {
            DispatchQueue.main.async { [weak self] in
                guard let self, !self.closed else { return }
                self.onAudioFormatChanged?()
            }
        }
    }

    var isChannelOpen: Bool { channel?.readyState == .open }

    @discardableResult
    func send(json object: [String: Any]) -> Bool {
        guard let channel, channel.readyState == .open,
              let data = try? JSONSerialization.data(withJSONObject: object) else { return false }
        return channel.sendData(RTCDataBuffer(data: data, isBinary: false))
    }

    /// Mic mute: local track disabled; the call (and billing) stays open.
    func setMicEnabled(_ enabled: Bool) { localTrack?.isEnabled = enabled }

    /// Quiet reply: disable remote audio tracks (playout silenced).
    func setRemoteAudioEnabled(_ enabled: Bool) {
        remoteMuted = !enabled
        applyRemoteMute()
    }

    private func applyRemoteMute() {
        for receiver in peer?.receivers ?? [] where receiver.track?.kind == kRTCMediaStreamTrackKindAudio {
            receiver.track?.isEnabled = !remoteMuted
        }
    }

    var sdpLineCount: Int? {
        peer?.localDescription?.sdp.components(separatedBy: .newlines).filter { !$0.isEmpty }.count
    }

    func close() {
        guard !closed else { return }
        closed = true
        localTrack?.isEnabled = false
        channel?.delegate = nil
        channel?.close()
        peer?.close()
        channel = nil
        localTrack = nil
        peer = nil
        routeWarmer.release()
    }

    // MARK: RTCPeerConnectionDelegate (signaling thread → main)

    func peerConnection(_ peerConnection: RTCPeerConnection, didChange stateChanged: RTCSignalingState) {}
    func peerConnection(_ peerConnection: RTCPeerConnection, didAdd stream: RTCMediaStream) {}
    func peerConnection(_ peerConnection: RTCPeerConnection, didRemove stream: RTCMediaStream) {}
    func peerConnectionShouldNegotiate(_ peerConnection: RTCPeerConnection) {}
    func peerConnection(_ peerConnection: RTCPeerConnection, didChange newState: RTCIceConnectionState) {}
    func peerConnection(_ peerConnection: RTCPeerConnection, didChange newState: RTCIceGatheringState) {
        if newState == .complete { iceComplete?() }
    }
    func peerConnection(_ peerConnection: RTCPeerConnection, didGenerate candidate: RTCIceCandidate) {}
    func peerConnection(_ peerConnection: RTCPeerConnection, didRemove candidates: [RTCIceCandidate]) {}
    func peerConnection(_ peerConnection: RTCPeerConnection, didOpen dataChannel: RTCDataChannel) {}
    func peerConnection(_ peerConnection: RTCPeerConnection, didChange newState: RTCPeerConnectionState) {
        if newState == .failed {
            DispatchQueue.main.async { [weak self] in self?.onConnectionFailed?() }
        }
    }
    func peerConnection(_ peerConnection: RTCPeerConnection, didAdd rtpReceiver: RTCRtpReceiver, streams mediaStreams: [RTCMediaStream]) {
        DispatchQueue.main.async { [weak self] in self?.applyRemoteMute() }
    }

    // MARK: RTCDataChannelDelegate

    func dataChannelDidChangeState(_ dataChannel: RTCDataChannel) {
        let state = dataChannel.readyState
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            if state == .open { onChannelOpen?() }
            if state == .closed { onChannelClosed?() }
        }
    }

    func dataChannel(_ dataChannel: RTCDataChannel, didReceiveMessageWith buffer: RTCDataBuffer) {
        guard !buffer.isBinary else { return }
        let data = buffer.data
        DispatchQueue.main.async { [weak self] in self?.onMessage?(data) }
    }
}
