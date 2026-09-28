// swift-tools-version: 6.0
import PackageDescription

// SpeakeasyCore (pure logic) and SpeakeasyClient (server API, WebRTC call engine, call
// client, panel model) build for macOS and iOS; the Speakeasy Mac app stays macOS-only
// (it imports AppKit throughout). The iPhone app links the two libraries from its own project.
let package = Package(
    name: "Speakeasy",
    platforms: [.macOS(.v14), .iOS(.v17)],
    products: [
        .library(name: "SpeakeasyCore", targets: ["SpeakeasyCore"]),
        .library(name: "SpeakeasyClient", targets: ["SpeakeasyClient"]),
        .executable(name: "Speakeasy", targets: ["Speakeasy"]),
    ],
    dependencies: [
        .package(url: "https://github.com/stasel/WebRTC.git", exact: "153.0.0"),
        .package(url: "https://github.com/sparkle-project/Sparkle", from: "2.6.0"),
    ],
    targets: [
        .target(name: "SpeakeasyCore"),
        .target(name: "SpeakeasyClient", dependencies: [
            "SpeakeasyCore",
            .product(name: "WebRTC", package: "WebRTC"),
        ]),
        .executableTarget(name: "Speakeasy", dependencies: [
            "SpeakeasyCore",
            "SpeakeasyClient",
            .product(name: "WebRTC", package: "WebRTC"),
            .product(name: "Sparkle", package: "Sparkle"),
        ]),
        .testTarget(name: "SpeakeasyCoreTests", dependencies: ["SpeakeasyCore"], resources: [.copy("Contract")]),
    ],
    swiftLanguageModes: [.v5]
)
