
import CoreAudio
import Foundation

/// Puts a Bluetooth headset (AirPods) into its call mode *before* WebRTC opens audio.
///
/// Opening a Bluetooth headset's mic switches it from music mode to call mode, and
/// macOS drops its output rate (48 kHz -> 24 kHz on AirPods Pro). WebRTC's Mac audio
/// device sets up playout at the old rate and cannot adapt when the rate changes
/// underneath it ("Changing channels not supported"), so the assistant plays back at half
/// speed: the deep, slow voice. Holding the mic open first means WebRTC sees the
/// final call-mode format from the start. Held for the whole call, released on close.
final class AudioRouteWarmer {
    private var device = AudioObjectID(0)
    private var proc: AudioDeviceIOProcID?
    private var rateListener: AudioObjectPropertyListenerBlock?
    private var watchedOutput = AudioObjectID(0)
    private var rateAddress = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyNominalSampleRate,
                                                         mScope: kAudioObjectPropertyScopeGlobal,
                                                         mElement: kAudioObjectPropertyElementMain)

    /// True when the default input is a Bluetooth device (the case that switches modes).
    static func inputIsBluetooth() -> Bool {
        let input = DefaultAudioDeviceWatcher.defaultDevice(kAudioHardwarePropertyDefaultInputDevice)
        guard input != 0 else { return false }
        var transport = UInt32(0)
        var address = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyTransportType,
                                                 mScope: kAudioObjectPropertyScopeGlobal,
                                                 mElement: kAudioObjectPropertyElementMain)
        var size = UInt32(MemoryLayout<UInt32>.size)
        guard AudioObjectGetPropertyData(input, &address, 0, nil, &size, &transport) == noErr else { return false }
        return transport == kAudioDeviceTransportTypeBluetooth || transport == kAudioDeviceTransportTypeBluetoothLE
    }

    static func nominalRate(_ id: AudioObjectID) -> Float64 {
        var rate = Float64(0)
        var address = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyNominalSampleRate,
                                                 mScope: kAudioObjectPropertyScopeGlobal,
                                                 mElement: kAudioObjectPropertyElementMain)
        var size = UInt32(MemoryLayout<Float64>.size)
        AudioObjectGetPropertyData(id, &address, 0, nil, &size, &rate)
        return rate
    }

    /// Open the Bluetooth mic (silently; samples are discarded) and wait until the
    /// output rate has settled. No-op for wired/built-in devices. Max ~1.5 s.
    func warm() async {
        guard proc == nil, Self.inputIsBluetooth() else { return }
        device = DefaultAudioDeviceWatcher.defaultDevice(kAudioHardwarePropertyDefaultInputDevice)
        var newProc: AudioDeviceIOProcID?
        let status = AudioDeviceCreateIOProcIDWithBlock(&newProc, device, DispatchQueue.global(qos: .userInitiated)) { _, _, _, _, _ in }
        guard status == noErr, let newProc else { return }
        proc = newProc
        guard AudioDeviceStart(device, newProc) == noErr else { release(); return }
        let output = DefaultAudioDeviceWatcher.defaultDevice(kAudioHardwarePropertyDefaultOutputDevice)
        var last = Self.nominalRate(output)
        var stableSince = Date()
        let start = Date()
        while Date().timeIntervalSince(start) < 1.5 {
            try? await Task.sleep(nanoseconds: 50_000_000)
            let now = Self.nominalRate(output)
            if now != last { last = now; stableSince = Date() }
            // Settled: some time for the switch to begin, then no change for 300 ms.
            if Date().timeIntervalSince(start) >= 0.25 && Date().timeIntervalSince(stableSince) >= 0.3 { break }
        }
    }

    /// Call `onChange` (main queue) if the default output's rate changes after setup:
    /// WebRTC cannot follow that, so the caller reopens the audio.
    func watchOutputRate(onChange: @escaping @MainActor () -> Void) {
        stopWatching()
        let output = DefaultAudioDeviceWatcher.defaultDevice(kAudioHardwarePropertyDefaultOutputDevice)
        guard output != 0 else { return }
        let initial = Self.nominalRate(output)
        let block: AudioObjectPropertyListenerBlock = { _, _ in
            guard Self.nominalRate(output) != initial else { return }
            MainActor.assumeIsolated { onChange() }
        }
        watchedOutput = output
        rateListener = block
        AudioObjectAddPropertyListenerBlock(output, &rateAddress, DispatchQueue.main, block)
    }

    private func stopWatching() {
        if let rateListener, watchedOutput != 0 {
            AudioObjectRemovePropertyListenerBlock(watchedOutput, &rateAddress, DispatchQueue.main, rateListener)
        }
        rateListener = nil
        watchedOutput = 0
    }

    func release() {
        stopWatching()
        if let proc {
            AudioDeviceStop(device, proc)
            AudioDeviceDestroyIOProcID(device, proc)
        }
        proc = nil
    }

    deinit { release() }
}

