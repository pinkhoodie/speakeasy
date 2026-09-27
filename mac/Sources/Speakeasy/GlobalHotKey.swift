import Carbon.HIToolbox
import Foundation

/// Carbon global hot key. Each instance owns a unique hot-key ID so several
/// shortcuts (call toggle, in-call mute) can coexist; releasing the instance
/// unregisters it. `onRelease` enables hold gestures.
final class GlobalHotKey {
    private static var nextID: UInt32 = 1
    private var hotKey: EventHotKeyRef?
    private var handler: EventHandlerRef?
    private let identifier: UInt32
    private let onPress: () -> Void
    private let onRelease: (() -> Void)?

    init(keyCode: UInt32 = UInt32(kVK_Space), modifiers: UInt32 = UInt32(controlKey | optionKey),
         display: String = "Control-Option-Space", exclusive: Bool = false,
         onRelease: (() -> Void)? = nil, callback: @escaping () -> Void) throws {
        identifier = Self.nextID
        Self.nextID += 1
        self.onPress = callback
        self.onRelease = onRelease
        var eventTypes = [EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))]
        if onRelease != nil {
            eventTypes.append(EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyReleased)))
        }
        let opaque = Unmanaged.passUnretained(self).toOpaque()
        let status = InstallEventHandler(GetApplicationEventTarget(), { _, event, userData in
            guard let event, let userData else { return OSStatus(eventNotHandledErr) }
            var hotKeyID = EventHotKeyID()
            let result = GetEventParameter(event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID), nil, MemoryLayout<EventHotKeyID>.size, nil, &hotKeyID)
            let owner = Unmanaged<GlobalHotKey>.fromOpaque(userData).takeUnretainedValue()
            guard result == noErr, hotKeyID.signature == GlobalHotKey.signature, hotKeyID.id == owner.identifier else {
                return OSStatus(eventNotHandledErr)
            }
            let released = GetEventKind(event) == UInt32(kEventHotKeyReleased)
            DispatchQueue.main.async { released ? owner.onRelease?() : owner.onPress() }
            return noErr
        }, eventTypes.count, &eventTypes, opaque, &handler)
        guard status == noErr else { throw NSError(domain: "Speakeasy.HotKey", code: Int(status), userInfo: [NSLocalizedDescriptionKey: "Could not register global shortcut"]) }
        let hotKeyID = EventHotKeyID(signature: Self.signature, id: identifier)
        let options = exclusive ? OptionBits(kEventHotKeyExclusive) : 0
        let register = RegisterEventHotKey(keyCode, modifiers, hotKeyID, GetApplicationEventTarget(), options, &hotKey)
        guard register == noErr else {
            if let handler { RemoveEventHandler(handler) }
            throw NSError(domain: "Speakeasy.HotKey", code: Int(register), userInfo: [NSLocalizedDescriptionKey: "\(display) is already in use"])
        }
    }

    static let signature = OSType(0x53504B45)   // 'SPKE'

    /// True when an enabled macOS system shortcut (Spotlight, input sources,
    /// Mission Control, screenshots…) already uses this exact combination.
    static func collidesWithSystemShortcut(keyCode: UInt32, modifiers: UInt32) -> Bool {
        var unmanaged: Unmanaged<CFArray>?
        guard CopySymbolicHotKeys(&unmanaged) == noErr, let array = unmanaged?.takeRetainedValue() as? [[String: Any]] else {
            return false
        }
        let relevant = UInt32(cmdKey | shiftKey | optionKey | controlKey)
        return array.contains { entry in
            let enabled = (entry[kHISymbolicHotKeyEnabled as String] as? Bool) ?? false
            let code = (entry[kHISymbolicHotKeyCode as String] as? NSNumber)?.uint32Value
            let mods = (entry[kHISymbolicHotKeyModifiers as String] as? NSNumber)?.uint32Value ?? 0
            return enabled && code == keyCode && mods & relevant == modifiers & relevant
        }
    }

    deinit {
        if let hotKey { UnregisterEventHotKey(hotKey) }
        if let handler { RemoveEventHandler(handler) }
    }
}
