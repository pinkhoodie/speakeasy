#if os(macOS)
import CoreAudio
import Foundation

/// Calls `onChange` on the main queue when the Mac's default audio output or input
/// device changes (AirPods connecting, headphones unplugged, a pick in Control
/// Center). Bursts are coalesced: macOS usually switches output and input together.
final class DefaultAudioDeviceWatcher {
    private let onChange: @MainActor () -> Void
    private var pending: DispatchWorkItem?
    private var addresses: [AudioObjectPropertyAddress] = [
        AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDefaultOutputDevice,
                                   mScope: kAudioObjectPropertyScopeGlobal,
                                   mElement: kAudioObjectPropertyElementMain),
        AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDefaultInputDevice,
                                   mScope: kAudioObjectPropertyScopeGlobal,
                                   mElement: kAudioObjectPropertyElementMain),
    ]
    private lazy var listener: AudioObjectPropertyListenerBlock = { [weak self] _, _ in
        self?.changed()
    }
    private var lastOutput: AudioObjectID
    private var lastInput: AudioObjectID

    init(onChange: @escaping @MainActor () -> Void) {
        self.onChange = onChange
        lastOutput = Self.defaultDevice(kAudioHardwarePropertyDefaultOutputDevice)
        lastInput = Self.defaultDevice(kAudioHardwarePropertyDefaultInputDevice)
        for index in addresses.indices {
            AudioObjectAddPropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject), &addresses[index],
                                                DispatchQueue.main, listener)
        }
    }

    deinit {
        for index in addresses.indices {
            AudioObjectRemovePropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject), &addresses[index],
                                                   DispatchQueue.main, listener)
        }
    }

    private func changed() {
        pending?.cancel()
        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            let output = Self.defaultDevice(kAudioHardwarePropertyDefaultOutputDevice)
            let input = Self.defaultDevice(kAudioHardwarePropertyDefaultInputDevice)
            guard output != self.lastOutput || input != self.lastInput else { return }
            self.lastOutput = output
            self.lastInput = input
            MainActor.assumeIsolated { self.onChange() }
        }
        pending = work
        // Let the device finish switching (Bluetooth profiles settle) before reopening.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.7, execute: work)
    }

    static func defaultDevice(_ selector: AudioObjectPropertySelector) -> AudioObjectID {
        var address = AudioObjectPropertyAddress(mSelector: selector,
                                                 mScope: kAudioObjectPropertyScopeGlobal,
                                                 mElement: kAudioObjectPropertyElementMain)
        var device = AudioObjectID(0)
        var size = UInt32(MemoryLayout<AudioObjectID>.size)
        AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &device)
        return device
    }

    static func outputName() -> String? {
        let device = defaultDevice(kAudioHardwarePropertyDefaultOutputDevice)
        guard device != 0 else { return nil }
        var address = AudioObjectPropertyAddress(mSelector: kAudioObjectPropertyName,
                                                 mScope: kAudioObjectPropertyScopeGlobal,
                                                 mElement: kAudioObjectPropertyElementMain)
        var name: Unmanaged<CFString>?
        var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        let status = withUnsafeMutablePointer(to: &name) {
            AudioObjectGetPropertyData(device, &address, 0, nil, &size, $0)
        }
        guard status == noErr, let name else { return nil }
        return name.takeRetainedValue() as String
    }
}
#endif
