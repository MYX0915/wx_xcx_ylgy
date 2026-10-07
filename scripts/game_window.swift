import AppKit
import ApplicationServices

func fail(_ message: String) -> Never {
    fputs(message + "\n", stderr)
    exit(1)
}

func windows() -> [[String: Any]] {
    let items = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
    return items.filter {
        let owner = $0[kCGWindowOwnerName as String] as? String ?? ""
        return owner.contains("WeChat") || owner.contains("WeApp") || owner.contains("小程序") || owner.contains("微信")
    }
}

func describe(_ item: [String: Any]) -> [String: Any] {
    return ["id": item[kCGWindowNumber as String] ?? 0,
            "pid": item[kCGWindowOwnerPID as String] ?? 0,
            "owner": item[kCGWindowOwnerName as String] ?? "",
            "title": item[kCGWindowName as String] ?? "",
            "bounds": item[kCGWindowBounds as String] ?? [:]]
}

func emit(_ data: Any) {
    let bytes = try! JSONSerialization.data(withJSONObject: data, options: [.sortedKeys])
    print(String(data: bytes, encoding: .utf8)!)
}

let args = CommandLine.arguments
guard args.count >= 2 else { fail("Expected inspect, capture, focus, pointer, or click") }
if args[1] == "inspect" {
    emit(["screenRecording": CGPreflightScreenCaptureAccess(),
          "accessibility": AXIsProcessTrusted(), "windows": windows().map(describe)])
    exit(0)
}
if args[1] == "pointer" {
    let point = CGEvent(source: nil)!.location
    emit(["x": point.x, "y": point.y])
    exit(0)
}
guard args.count >= 3, let id = UInt32(args[2]),
      let item = windows().first(where: { ($0[kCGWindowNumber as String] as? UInt32) == id }),
      let pid = item[kCGWindowOwnerPID as String] as? Int32,
      let boundsData = item[kCGWindowBounds as String] as? [String: Any],
      let bounds = CGRect(dictionaryRepresentation: boundsData as CFDictionary)
else { fail("Game window is no longer available") }

if args[1] == "focus" {
    guard let app = NSRunningApplication(processIdentifier: pid) else { fail("Application not found") }
    app.activate(options: [])
    emit(describe(item))
} else if args[1] == "capture" {
    guard args.count == 4, CGPreflightScreenCaptureAccess() else { fail("Screen recording permission is required") }
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
    process.arguments = ["-x", "-o", "-l", String(id), args[3]]
    try process.run()
    process.waitUntilExit()
    guard process.terminationStatus == 0 else { fail("Window screenshot failed") }
    emit(describe(item))
} else if args[1] == "click" {
    guard args.count == 5, let x = Double(args[3]), let y = Double(args[4]),
          bounds.contains(CGPoint(x: x, y: y)), AXIsProcessTrusted()
    else { fail("Invalid click coordinates or missing accessibility permission") }
    guard NSWorkspace.shared.frontmostApplication?.processIdentifier == pid else { fail("Game window lost focus") }
    let topWindow = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
    let covering = topWindow.first { entry in
        guard let raw = entry[kCGWindowBounds as String] as? [String: Any],
              let rect = CGRect(dictionaryRepresentation: raw as CFDictionary) else { return false }
        return rect.contains(CGPoint(x: x, y: y)) && (entry[kCGWindowAlpha as String] as? Double ?? 1) > 0
    }
    if (covering?[kCGWindowNumber as String] as? UInt32) != id {
        // Dock can expose a full-screen transparent window; verify the actual hit window.
        var hit: AXUIElement?
        var rawWindow: CFTypeRef?
        guard AXUIElementCopyElementAtPosition(AXUIElementCreateSystemWide(), Float(x), Float(y), &hit) == .success,
              let hit = hit,
              AXUIElementCopyAttributeValue(hit, kAXWindowAttribute as CFString, &rawWindow) == .success,
              let rawWindow = rawWindow, CFGetTypeID(rawWindow) == AXUIElementGetTypeID()
        else { fail("Cannot verify the window at the click target") }
        let hitWindow = rawWindow as! AXUIElement
        var hitPID: pid_t = 0
        var positionValue: CFTypeRef?
        var sizeValue: CFTypeRef?
        guard AXUIElementGetPid(hitWindow, &hitPID) == .success, hitPID == pid,
              AXUIElementCopyAttributeValue(hitWindow, kAXPositionAttribute as CFString, &positionValue) == .success,
              AXUIElementCopyAttributeValue(hitWindow, kAXSizeAttribute as CFString, &sizeValue) == .success,
              let positionValue = positionValue, CFGetTypeID(positionValue) == AXValueGetTypeID(),
              let sizeValue = sizeValue, CFGetTypeID(sizeValue) == AXValueGetTypeID()
        else { fail("Click target is covered by another window") }
        var origin = CGPoint.zero
        var size = CGSize.zero
        guard AXValueGetValue(positionValue as! AXValue, .cgPoint, &origin),
              AXValueGetValue(sizeValue as! AXValue, .cgSize, &size),
              abs(origin.x - bounds.origin.x) < 1, abs(origin.y - bounds.origin.y) < 1,
              abs(size.width - bounds.width) < 1, abs(size.height - bounds.height) < 1
        else { fail("Click target window does not match the game bounds") }
    }
    let point = CGPoint(x: x, y: y)
    CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: point, mouseButton: .left)?.post(tap: .cghidEventTap)
    CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown, mouseCursorPosition: point, mouseButton: .left)?.post(tap: .cghidEventTap)
    usleep(70000)
    CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: point, mouseButton: .left)?.post(tap: .cghidEventTap)
    emit(["clicked": true, "x": x, "y": y])
} else {
    fail("Unknown command")
}
