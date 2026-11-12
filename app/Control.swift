import Cocoa
import ApplicationServices
import AVFoundation
import ServiceManagement

/// Mike's hands: reads any app's interface through the Accessibility API and drives it with
/// real mouse and keyboard events. Runs inside Mike.app, so the one Accessibility switch for
/// "Mike" covers everything. Called by the backend through the web view bridge (op + args → JSON).
final class Control {
    static let shared = Control()

    private var elements: [AXUIElement] = []      // ids handed out by the last `ui` call
    private var lastApp: NSRunningApplication?    // last app in front that wasn't Mike
    private var woken: Set<pid_t> = []            // apps whose full accessibility tree we've switched on
    private let queue = DispatchQueue(label: "mike.control", qos: .userInitiated)

    init() {
        lastApp = NSWorkspace.shared.frontmostApplication
        NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main) { [weak self] n in
            if let app = n.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication,
               app.processIdentifier != ProcessInfo.processInfo.processIdentifier {
                self?.lastApp = app
            }
        }
    }

    func run(_ op: String, _ a: [String: Any], done: @escaping ([String: Any]) -> Void) {
        queue.async {
            let out = self.handle(op, a)
            DispatchQueue.main.async { done(out) }
        }
    }

    // MARK: dispatch

    private func handle(_ op: String, _ a: [String: Any]) -> [String: Any] {
        switch op {
        case "context": return context()
        case "perm_status": return permStatus()
        case "open_pane": return openPane(a["pane"] as? String ?? "")
        case "login_item": return loginItem(a["enable"] as? Bool)
        case "show_at_launch":
            if let on = a["enable"] as? Bool { UserDefaults.standard.set(on, forKey: "showWindowAtLaunch") }
            return ["enabled": UserDefaults.standard.bool(forKey: "showWindowAtLaunch")]
        case "open_url":
            if let s = a["url"] as? String, let url = URL(string: s), ["https", "http"].contains(url.scheme ?? "") {
                DispatchQueue.main.async { NSWorkspace.shared.open(url) }
                return ["ok": true]
            }
            return ["error": "bad url"]
        case "open_path":
            let path = (a["path"] as? String ?? "") as NSString
            let url = URL(fileURLWithPath: path.expandingTildeInPath)
            DispatchQueue.main.async {
                if a["reveal"] as? Bool == true { NSWorkspace.shared.activateFileViewerSelecting([url]) }
                else { NSWorkspace.shared.open(url) }
            }
            return ["ok": true]
        case "app_info":
            let info = Bundle.main.infoDictionary ?? [:]
            return ["version": info["CFBundleShortVersionString"] as? String ?? "1.0",
                    "build": info["CFBundleVersion"] as? String ?? "1",
                    "macos": ProcessInfo.processInfo.operatingSystemVersionString]
        case "automation": return automation(a["app"] as? String ?? "")
        case "media": return media(a["action"] as? String ?? "", times: max(1, a["times"] as? Int ?? 1))
        case "apps": return apps()
        case "activate": return activate(a["app"] as? String ?? "")
        default: break
        }
        guard AXIsProcessTrustedWithOptions([kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary) else {
            return ["error": "Mike needs Accessibility access. Tell the user in one sentence: System Settings, Privacy & Security, Accessibility, turn on Mike."]
        }
        switch op {
        case "ui": return ui(app: a["app"] as? String, query: a["query"] as? String)
        case "press": return press(a["id"] as? Int ?? -1)
        case "set_text": return setText(a["id"] as? Int ?? -1, a["text"] as? String ?? "")
        case "click":
            let x = num(a["x"]), y = num(a["y"])
            click(CGPoint(x: x, y: y), count: (a["double"] as? Bool ?? false) ? 2 : 1, right: a["right"] as? Bool ?? false)
            return ["ok": true]
        case "type": typeText(a["text"] as? String ?? ""); return ["ok": true]
        case "key": return key(a["key"] as? String ?? "", a["modifiers"] as? [String] ?? [])
        case "scroll":
            scroll(dx: Int32(num(a["dx"])), dy: Int32(num(a["dy"])), at: a["x"] != nil ? CGPoint(x: num(a["x"]), y: num(a["y"])) : nil)
            return ["ok": true]
        case "menu": return menu(a["path"] as? [String] ?? [], app: a["app"] as? String)
        default: return ["error": "unknown op \(op)"]
        }
    }

    private func num(_ v: Any?) -> Double { (v as? Double) ?? Double(v as? Int ?? 0) }

    // MARK: apps and context

    private func target(_ name: String?) -> NSRunningApplication? {
        if let name = name?.lowercased(), !name.isEmpty {
            let apps = NSWorkspace.shared.runningApplications.filter { $0.activationPolicy == .regular }
            return apps.first { $0.localizedName?.lowercased() == name }
                ?? apps.first { $0.localizedName?.lowercased().contains(name) ?? false }
        }
        let me = ProcessInfo.processInfo.processIdentifier
        if let f = NSWorkspace.shared.frontmostApplication, f.processIdentifier != me { return f }
        if let l = lastApp, l.processIdentifier != me, !l.isTerminated { return l }
        // Mike itself is in front: use the app owning the topmost normal window on screen.
        let info = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
        for w in info where (w[kCGWindowLayer as String] as? Int) == 0 {
            if let pid = w[kCGWindowOwnerPID as String] as? pid_t, pid != me,
               let app = NSRunningApplication(processIdentifier: pid), app.activationPolicy == .regular {
                return app
            }
        }
        return nil
    }

    private func apps() -> [String: Any] {
        let names = NSWorkspace.shared.runningApplications
            .filter { $0.activationPolicy == .regular }.compactMap { $0.localizedName }
        return ["front": target(nil)?.localizedName ?? "", "running": names]
    }

    private func activate(_ name: String) -> [String: Any] {
        guard let app = target(name) else { return ["error": "\(name) isn't running; open it with `open -a`."] }
        app.activate(options: [.activateAllWindows])
        lastApp = app
        return ["ok": true, "app": app.localizedName ?? name]
    }

    /// Cheap snapshot of what the user is doing: front app, window title, selected text.
    private func context() -> [String: Any] {
        guard let app = target(nil) else { return [:] }
        var out: [String: Any] = ["app": app.localizedName ?? ""]
        guard AXIsProcessTrusted() else { return out }
        let ax = AXUIElementCreateApplication(app.processIdentifier)
        AXUIElementSetMessagingTimeout(ax, 0.3)
        if let win = attr(ax, kAXFocusedWindowAttribute) {
            let title = str(win as! AXUIElement, kAXTitleAttribute)
            if !title.isEmpty { out["window"] = String(title.prefix(120)) }
        }
        if let focused = attr(ax, kAXFocusedUIElementAttribute) {
            let sel = str(focused as! AXUIElement, kAXSelectedTextAttribute)
            if !sel.isEmpty { out["selected"] = String(sel.prefix(1500)) }
        }
        return out
    }

    // MARK: settings helpers

    private func permStatus() -> [String: Any] {
        func av(_ t: AVMediaType) -> String {
            switch AVCaptureDevice.authorizationStatus(for: t) {
            case .authorized: return "granted"
            case .denied, .restricted: return "denied"
            default: return "not_asked"
            }
        }
        return ["microphone": av(.audio), "camera": av(.video),
                "screen": CGPreflightScreenCaptureAccess() ? "granted" : "denied",
                "accessibility": AXIsProcessTrusted() ? "granted" : "denied"]
    }

    private func openPane(_ pane: String) -> [String: Any] {
        let anchors = ["microphone": "Privacy_Microphone", "camera": "Privacy_Camera", "screen": "Privacy_ScreenCapture",
                       "accessibility": "Privacy_Accessibility", "automation": "Privacy_Automation"]
        guard let anchor = anchors[pane],
              let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?\(anchor)") else { return ["error": "unknown pane"] }
        DispatchQueue.main.async { NSWorkspace.shared.open(url) }
        return ["ok": true]
    }

    private func loginItem(_ enable: Bool?) -> [String: Any] {
        guard #available(macOS 13.0, *) else { return ["error": "Needs macOS 13 or later."] }
        let svc = SMAppService.mainApp
        if let on = enable {
            do { if on { try svc.register() } else { try svc.unregister() } }
            catch { return ["enabled": svc.status == .enabled, "error": error.localizedDescription] }
        }
        return ["enabled": svc.status == .enabled, "needs_approval": svc.status == .requiresApproval]
    }

    // MARK: AppleScript permission check (never shows a dialog)

    private func automation(_ name: String) -> [String: Any] {
        var bid: String? = nil
        if name.contains(".") && !name.contains(" ") { bid = name }
        if bid == nil, let path = NSWorkspace.shared.fullPath(forApplication: name) { bid = Bundle(path: path)?.bundleIdentifier }
        if bid == nil, name.lowercased() == "system events" { bid = "com.apple.systemevents" }
        guard let id = bid else { return ["status": "unknown_app"] }
        var addr = AEAddressDesc()
        let made = id.withCString { AECreateDesc(DescType(typeApplicationBundleID), $0, strlen($0), &addr) }
        guard made == noErr else { return ["status": "unknown_app"] }
        defer { AEDisposeDesc(&addr) }
        let r = AEDeterminePermissionToAutomateTarget(&addr, AEEventClass(typeWildCard), AEEventID(typeWildCard), false)
        switch r {
        case noErr: return ["status": "granted", "bundle": id]
        case OSStatus(errAEEventNotPermitted): return ["status": "denied", "bundle": id]
        case OSStatus(errAEEventWouldRequireUserConsent): return ["status": "ask", "bundle": id]
        case OSStatus(procNotFound): return ["status": "not_running", "bundle": id]
        default: return ["status": "unknown", "code": Int(r), "bundle": id]
        }
    }

    // MARK: media / hardware keys (no permission needed)

    private static let mediaKeys: [String: Int32] = [
        "play_pause": 16, "play": 16, "pause": 16, "next": 17, "previous": 18,
        "volume_up": 0, "volume_down": 1, "mute": 7, "brightness_up": 2, "brightness_down": 3]

    private func media(_ action: String, times: Int) -> [String: Any] {
        guard let code = Control.mediaKeys[action] else {
            return ["error": "Unknown media action. Use: " + Control.mediaKeys.keys.sorted().joined(separator: ", ")]
        }
        for _ in 0..<min(times, 16) {
            for down in [true, false] {
                let flags = NSEvent.ModifierFlags(rawValue: down ? 0xa00 : 0xb00)
                let data1 = Int((code << 16) | ((down ? 0xa : 0xb) << 8))
                let ev = NSEvent.otherEvent(with: .systemDefined, location: .zero, modifierFlags: flags, timestamp: 0,
                                            windowNumber: 0, context: nil, subtype: 8, data1: data1, data2: -1)
                ev?.cgEvent?.post(tap: .cghidEventTap)
            }
            usleep(40_000)
        }
        return ["ok": true]
    }

    // MARK: reading the interface

    private func attr(_ el: AXUIElement, _ name: String) -> AnyObject? {
        var v: AnyObject?
        return AXUIElementCopyAttributeValue(el, name as CFString, &v) == .success ? v : nil
    }

    private func str(_ el: AXUIElement, _ name: String) -> String {
        guard let v = attr(el, name) else { return "" }
        if let s = v as? String { return s.trimmingCharacters(in: .whitespacesAndNewlines) }
        if let n = v as? NSNumber { return n.stringValue }
        return ""
    }

    private func frame(_ el: AXUIElement) -> CGRect? {
        guard let p = attr(el, kAXPositionAttribute), let s = attr(el, kAXSizeAttribute) else { return nil }
        var pt = CGPoint.zero, sz = CGSize.zero
        AXValueGetValue(p as! AXValue, .cgPoint, &pt)
        AXValueGetValue(s as! AXValue, .cgSize, &sz)
        return CGRect(origin: pt, size: sz)
    }

    private static let actionable: Set<String> = [
        "AXButton", "AXCheckBox", "AXRadioButton", "AXPopUpButton", "AXMenuButton", "AXTextField",
        "AXTextArea", "AXComboBox", "AXSlider", "AXLink", "AXMenuItem", "AXCell", "AXRow", "AXTab",
        "AXDisclosureTriangle", "AXIncrementor", "AXSearchField", "AXSwitch", "AXSegmentedControl"]
    private static let skip: Set<String> = ["AXMenuBar", "AXScrollBar"]

    /// Lists the labelled elements of an app's front window with ids for press/set_text.
    private func ui(app name: String?, query: String?) -> [String: Any] {
        guard let app = target(name) else { return ["error": "No app called \(name ?? "?") is running."] }
        let ax = AXUIElementCreateApplication(app.processIdentifier)
        AXUIElementSetMessagingTimeout(ax, 1.5)
        // Electron/Chromium apps (Spotify, Discord, VS Code, Chrome, Opera...) only build their
        // accessibility tree when an assistive app asks for it.
        if !woken.contains(app.processIdentifier) {
            woken.insert(app.processIdentifier)
            let a = AXUIElementSetAttributeValue(ax, "AXManualAccessibility" as CFString, kCFBooleanTrue)
            let b = AXUIElementSetAttributeValue(ax, "AXEnhancedUserInterface" as CFString, kCFBooleanTrue)
            if a == .success || b == .success { usleep(600_000) }
        }
        var roots: [AXUIElement] = []
        if let w = attr(ax, kAXFocusedWindowAttribute) { roots.append(w as! AXUIElement) }
        else if let ws = attr(ax, kAXWindowsAttribute) as? [AXUIElement], let w = ws.first { roots.append(w) }
        if roots.isEmpty { return ["app": app.localizedName ?? "", "error": "That app has no open window. Use the menu tool or open a window first."] }

        let q = query?.lowercased().trimmingCharacters(in: .whitespaces) ?? ""
        let screen = NSScreen.screens.first?.frame ?? .zero
        elements = []
        var lines: [String] = []
        var visited = 0
        var stack: [(AXUIElement, Int, String)] = roots.map { ($0, 0, "") }
        while let (el, depth, inherited) = stack.popLast(), visited < 4000, lines.count < 220 {
            visited += 1
            let role = str(el, kAXRoleAttribute)
            if Control.skip.contains(role) { continue }
            let subrole = str(el, kAXSubroleAttribute)
            var label = [kAXTitleAttribute, kAXDescriptionAttribute, "AXPlaceholderValue", kAXHelpAttribute]
                .lazy.map { self.str(el, $0) }.first { !$0.isEmpty } ?? ""
            let value = str(el, kAXValueAttribute)
            if label.isEmpty && role == "AXStaticText" { label = value }
            // Rows and cells usually carry their name in a text child: name them after it.
            if label.isEmpty && ["AXRow", "AXCell"].contains(role) { label = innerText(el, 3) }
            if role == "AXStaticText" && !inherited.isEmpty && label == inherited { pushKids(el, depth, inherited, &stack); continue }
            if role == "AXCell" && (label.isEmpty || label == inherited) { pushKids(el, depth, inherited, &stack); continue }
            let isAction = Control.actionable.contains(role)
            if let f = frame(el), f.width > 1, f.height > 1, f.intersects(screen.insetBy(dx: -2, dy: -2)),
               isAction || !label.isEmpty {
                var text = "\(role.dropFirst(2))\(subrole.isEmpty || subrole == "AXUnknown" ? "" : "/" + subrole.dropFirst(2))"
                if !label.isEmpty { text += " \"\(label.prefix(80))\"" }
                if !value.isEmpty && value != label && role != "AXStaticText" { text += " value=\"\(value.prefix(60))\"" }
                if attr(el, kAXEnabledAttribute) as? Bool == false { text += " (disabled)" }
                if ["AXTextField", "AXTextArea", "AXComboBox"].contains(role), attr(el, kAXFocusedAttribute) as? Bool == true { text += " (focused)" }
                if q.isEmpty || text.lowercased().contains(q) {
                    elements.append(el)
                    lines.append("[\(elements.count - 1)] \(text) @\(Int(f.midX)),\(Int(f.midY))")
                }
            }
            pushKids(el, depth, ["AXRow", "AXCell"].contains(role) && !label.isEmpty ? label : inherited, &stack)
        }
        let title = str(roots[0], kAXTitleAttribute)
        return ["app": app.localizedName ?? "", "window": title, "elements": lines.joined(separator: "\n"),
                "note": lines.count >= 220 ? "List cut at 220 items; pass a query to narrow it." : ""]
    }

    private func pushKids(_ el: AXUIElement, _ depth: Int, _ label: String, _ stack: inout [(AXUIElement, Int, String)]) {
        guard depth < 40, let kids = attr(el, kAXChildrenAttribute) as? [AXUIElement] else { return }
        for k in kids.reversed() { stack.append((k, depth + 1, label)) }
    }

    /// First bit of visible text inside an element (for unnamed rows/cells).
    private func innerText(_ el: AXUIElement, _ depth: Int) -> String {
        guard depth > 0, let kids = attr(el, kAXChildrenAttribute) as? [AXUIElement] else { return "" }
        for k in kids.prefix(6) {
            let r = str(k, kAXRoleAttribute)
            if r == "AXStaticText" { let v = str(k, kAXValueAttribute); if !v.isEmpty { return v } }
            let t = str(k, kAXTitleAttribute); if !t.isEmpty && r != "AXImage" { return t }
            let deeper = innerText(k, depth - 1); if !deeper.isEmpty { return deeper }
        }
        return ""
    }

    // MARK: acting

    private func element(_ id: Int) -> AXUIElement? { id >= 0 && id < elements.count ? elements[id] : nil }

    private func press(_ id: Int) -> [String: Any] {
        guard let el = element(id) else { return ["error": "Unknown element id; call ui again."] }
        let role = str(el, kAXRoleAttribute)
        if ["AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"].contains(role) {
            AXUIElementSetAttributeValue(el, kAXFocusedAttribute as CFString, kCFBooleanTrue)
        }
        if AXUIElementPerformAction(el, kAXPressAction as CFString) == .success { return ["ok": true, "how": "pressed"] }
        if let f = frame(el) {
            click(CGPoint(x: f.midX, y: f.midY), count: 1, right: false)
            return ["ok": true, "how": "clicked"]
        }
        return ["error": "Couldn't press that element."]
    }

    private func setText(_ id: Int, _ text: String) -> [String: Any] {
        guard let el = element(id) else { return ["error": "Unknown element id; call ui again."] }
        AXUIElementSetAttributeValue(el, kAXFocusedAttribute as CFString, kCFBooleanTrue)
        if AXUIElementSetAttributeValue(el, kAXValueAttribute as CFString, text as CFString) == .success,
           str(el, kAXValueAttribute) == text {
            return ["ok": true]
        }
        // Web fields and custom controls: click in, select all, type.
        if let f = frame(el) { click(CGPoint(x: f.midX, y: f.midY), count: 1, right: false) }
        usleep(120_000)
        _ = key("a", ["command"])
        typeText(text)
        return ["ok": true, "how": "typed"]
    }

    private func post(_ e: CGEvent?) { e?.post(tap: .cghidEventTap) }

    private func click(_ p: CGPoint, count: Int, right: Bool) {
        let (down, up, btn): (CGEventType, CGEventType, CGMouseButton) =
            right ? (.rightMouseDown, .rightMouseUp, .right) : (.leftMouseDown, .leftMouseUp, .left)
        post(CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: p, mouseButton: .left))
        usleep(30_000)
        for i in 1...max(1, count) {
            for t in [down, up] {
                let e = CGEvent(mouseEventSource: nil, mouseType: t, mouseCursorPosition: p, mouseButton: btn)
                e?.setIntegerValueField(.mouseEventClickState, value: Int64(i))
                post(e)
                usleep(15_000)
            }
            usleep(40_000)
        }
    }

    private func typeText(_ text: String) {
        for (i, line) in text.components(separatedBy: "\n").enumerated() {
            if i > 0 { _ = key("return", []) }
            let units = Array(line.utf16)
            var start = 0
            while start < units.count {
                let chunk = Array(units[start..<min(start + 16, units.count)])
                for down in [true, false] {
                    let e = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: down)
                    e?.keyboardSetUnicodeString(stringLength: chunk.count, unicodeString: chunk)
                    post(e)
                }
                start += 16
                usleep(8_000)
            }
        }
    }

    private static let keyCodes: [String: CGKeyCode] = [
        "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12,
        "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23,
        "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34,
        "p": 35, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46,
        ".": 47, "`": 50, "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51, "backspace": 51,
        "escape": 53, "esc": 53, "forwarddelete": 117, "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
        "left": 123, "right": 124, "down": 125, "up": 126, "f1": 122, "f2": 120, "f3": 99, "f4": 118,
        "f5": 96, "f6": 97, "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111]

    private func key(_ name: String, _ mods: [String]) -> [String: Any] {
        guard let code = Control.keyCodes[name.lowercased()] else { return ["error": "Unknown key \(name)."] }
        var flags = CGEventFlags()
        for m in mods.map({ $0.lowercased() }) {
            switch m {
            case "command", "cmd": flags.insert(.maskCommand)
            case "option", "alt": flags.insert(.maskAlternate)
            case "shift": flags.insert(.maskShift)
            case "control", "ctrl": flags.insert(.maskControl)
            case "fn": flags.insert(.maskSecondaryFn)
            default: break
            }
        }
        for down in [true, false] {
            let e = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: down)
            e?.flags = flags
            post(e)
            usleep(12_000)
        }
        return ["ok": true]
    }

    private func scroll(dx: Int32, dy: Int32, at p: CGPoint?) {
        if let p = p { post(CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: p, mouseButton: .left)) }
        post(CGEvent(scrollWheelEvent2Source: nil, units: .line, wheelCount: 2, wheel1: dy, wheel2: dx, wheel3: 0))
    }

    /// Clicks a menu bar item by path, e.g. ["File", "New Window"]. Lists choices when a step isn't found.
    private func menu(_ path: [String], app name: String?) -> [String: Any] {
        guard let app = target(name) else { return ["error": "App not running."] }
        let ax = AXUIElementCreateApplication(app.processIdentifier)
        AXUIElementSetMessagingTimeout(ax, 1.5)
        guard let bar = attr(ax, kAXMenuBarAttribute) else { return ["error": "No menu bar."] }
        var level = bar as! AXUIElement
        func norm(_ s: String) -> String { s.lowercased().replacingOccurrences(of: "…", with: "").replacingOccurrences(of: "...", with: "").trimmingCharacters(in: .whitespaces) }
        for (i, step) in path.enumerated() {
            var items = attr(level, kAXChildrenAttribute) as? [AXUIElement] ?? []
            // Menu items live inside an AXMenu child.
            if items.count == 1, str(items[0], kAXRoleAttribute) == "AXMenu" {
                items = attr(items[0], kAXChildrenAttribute) as? [AXUIElement] ?? []
            }
            let titles = items.map { str($0, kAXTitleAttribute) }
            guard let idx = titles.firstIndex(where: { norm($0) == norm(step) })
                    ?? titles.firstIndex(where: { norm($0).hasPrefix(norm(step)) && !norm(step).isEmpty }) else {
                return ["error": "No menu item \"\(step)\".", "choices": titles.filter { !$0.isEmpty }]
            }
            let item = items[idx]
            if i == path.count - 1 {
                if attr(item, kAXEnabledAttribute) as? Bool == false { return ["error": "\"\(step)\" is greyed out."] }
                AXUIElementPerformAction(item, kAXPressAction as CFString)
                return ["ok": true]
            }
            level = item
        }
        return ["error": "Empty path."]
    }
}
