import Foundation

public enum PCM {
    public static func float32ToPCM16(_ samples: [Float]) -> Data {
        var output = Data(capacity: samples.count * 2)
        for sample in samples {
            let clipped = max(-1, min(1, sample))
            var value = Int16(clipped * Float(Int16.max)).littleEndian
            withUnsafeBytes(of: &value) { output.append(contentsOf: $0) }
        }
        return output
    }

    public static func resample(_ samples: [Float], from sourceRate: Double, to targetRate: Double = 24_000) -> [Float] {
        guard !samples.isEmpty, sourceRate > 0, targetRate > 0, sourceRate != targetRate else { return samples }
        let count = max(1, Int((Double(samples.count) * targetRate / sourceRate).rounded(.down)))
        var output = [Float](); output.reserveCapacity(count)
        let scale = sourceRate / targetRate
        for i in 0..<count {
            let position = Double(i) * scale
            let lower = min(Int(position), samples.count - 1)
            let upper = min(lower + 1, samples.count - 1)
            let fraction = Float(position - Double(lower))
            output.append(samples[lower] * (1 - fraction) + samples[upper] * fraction)
        }
        return output
    }

    public static func wav(pcm16: Data, sampleRate: Int = 24_000, channels: Int = 1) -> Data {
        var data = Data()
        func ascii(_ value: String) { data.append(value.data(using: .ascii)!) }
        func u16(_ value: UInt16) { var x = value.littleEndian; withUnsafeBytes(of: &x) { data.append(contentsOf: $0) } }
        func u32(_ value: UInt32) { var x = value.littleEndian; withUnsafeBytes(of: &x) { data.append(contentsOf: $0) } }
        ascii("RIFF"); u32(UInt32(36 + pcm16.count)); ascii("WAVE")
        ascii("fmt "); u32(16); u16(1); u16(UInt16(channels)); u32(UInt32(sampleRate))
        u32(UInt32(sampleRate * channels * 2)); u16(UInt16(channels * 2)); u16(16)
        ascii("data"); u32(UInt32(pcm16.count)); data.append(pcm16)
        return data
    }
}
