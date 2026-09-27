// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "Speakeasy",
    platforms: [.macOS(.v14)],
    products: [
        .library(name: "SpeakeasyCore", targets: ["SpeakeasyCore"]),
        .executable(name: "Speakeasy", targets: ["Speakeasy"]),
    ],
    dependencies: [
        .package(url: "https://github.com/stasel/WebRTC.git", exact: "153.0.0"),
    ],
    targets: [
        .target(name: "SpeakeasyCore"),
        .executableTarget(name: "Speakeasy", dependencies: [
            "SpeakeasyCore",
            .product(name: "WebRTC", package: "WebRTC"),
        ]),
        .testTarget(name: "SpeakeasyCoreTests", dependencies: ["SpeakeasyCore"], resources: [.copy("Contract")]),
    ],
    swiftLanguageModes: [.v5]
)
