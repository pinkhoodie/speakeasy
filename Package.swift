// swift-tools-version: 6.0
import PackageDescription

// The shared Speakeasy libraries for apps that depend on this repo by URL (the iPhone/iPad app).
// SpeakeasyCore is pure logic; SpeakeasyClient is the server API, WebRTC call engine, call client
// and panel model. They build for macOS and iOS. The Mac app itself builds from mac/Package.swift.
let package = Package(
    name: "Speakeasy",
    platforms: [.macOS(.v14), .iOS(.v17)],
    products: [
        .library(name: "SpeakeasyCore", targets: ["SpeakeasyCore"]),
        .library(name: "SpeakeasyClient", targets: ["SpeakeasyClient"]),
    ],
    dependencies: [
        .package(url: "https://github.com/stasel/WebRTC.git", exact: "153.0.0"),
    ],
    targets: [
        .target(name: "SpeakeasyCore", path: "mac/Sources/SpeakeasyCore"),
        .target(name: "SpeakeasyClient", dependencies: [
            "SpeakeasyCore",
            .product(name: "WebRTC", package: "WebRTC"),
        ], path: "mac/Sources/SpeakeasyClient"),
    ],
    swiftLanguageModes: [.v5]
)
