// Mike.app — native shell for Mike.
// Starts Mike's Python backend (if it isn't already running), shows the interface in its own
// window, lives in the menu bar, and listens for ⌥Space from anywhere.
import Carbon.HIToolbox
import Cocoa
import ScreenCaptureKit
import WebKit

let mikeHome = (Bundle.main.object(forInfoDictionaryKey: "MikeHome") as? String) ?? (NSHomeDirectory() + "/mike")
let mikePort = (Bundle.main.object(forInfoDictionaryKey: "MikePort") as? Int) ?? 8777
let mikeURL = URL(string: "http://127.0.0.1:\(mikePort)/")!

private weak var sharedDelegate: AppDelegate?

/// Transparent strip across the top of the window so it can be dragged like a title bar.
/// The page reports where its top-bar buttons are ("holes"); clicks there go to the page instead.
final class DragStrip: NSView {
    var holes: [NSRect] = []          // page coordinates (top-left origin)
    override var isFlipped: Bool { true }
    override func hitTest(_ point: NSPoint) -> NSView? {
        let p = convert(point, from: superview)
        guard bounds.contains(p) else { return nil }
        if p.x < 80 && p.y < 30 { return nil }                 // traffic-light buttons
        if holes.contains(where: { $0.contains(p) }) { return nil }
        return self
    }
    override func mouseDown(with event: NSEvent) {
        if event.clickCount == 2 { window?.performZoom(nil) } else { window?.performDrag(with: event) }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKUIDelegate,
    WKNavigationDelegate, WKScriptMessageHandler, NSMenuDelegate
{
    let petController = PetController()
    let dragStrip = DragStrip()
    var window: NSWindow!
    var web: WKWebView!
    var server: Process?
    var statusItem: NSStatusItem!
    var stateItem: NSMenuItem!
    var micItem: NSMenuItem!
    static let surfaceDark = NSColor(srgbRed: 0x26 / 255.0, green: 0x26 / 255.0, blue: 0x24 / 255.0, alpha: 1)
    static let surfaceLight = NSColor(srgbRed: 0xFA / 255.0, green: 0xF9 / 255.0, blue: 0xF5 / 255.0, alpha: 1)
    var surfaceIsDark = true
    var hotKeyRef: EventHotKeyRef?
    var quitHotKeyRef: EventHotKeyRef?
    var micHotKeyRef: EventHotKeyRef?
    var micOn = true
    var lastState = "offline"
    var terminating = false
    var reallyQuit = false      // only the explicit "Quit Mike" commands turn Mike off

    // MARK: lifecycle
    func applicationDidFinishLaunching(_ notification: Notification) {
        sharedDelegate = self
        NSWorkspace.shared.notificationCenter.addObserver(forName: NSWorkspace.willPowerOffNotification, object: nil, queue: .main) { _ in
            sharedDelegate?.reallyQuit = true     // logging out / shutting down: never block it
        }
        buildMainMenu()
        buildWindow()
        petController.setup(app: self)
        buildStatusItem()
        registerHotKey()
        showLoading("Waking Mike…")
        ensureServer()
        // Pet-first: the window stays hidden. Open it from the Dock, the menu bar, or by double-clicking the pet.
        if UserDefaults.standard.bool(forKey: "showWindowAtLaunch") { showWindow() } else { setGhost(true) }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showWindow()
        return true
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    /// ⌘Q or Dock > Quit only tuck Mike away, so he keeps listening for his name. Turning him off
    /// takes an explicit "Quit Mike" (menu bar, pet menu, ⌥⇧Q, or "Mike, quit"). Logout/shutdown always quits.
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if reallyQuit { return .terminateNow }
        setGhost(true)
        return .terminateCancel
    }

    @objc func quitCompletely() {
        reallyQuit = true
        NSApp.terminate(nil)
    }

    @objc func closeToBackground() { setGhost(true) }

    func applicationWillTerminate(_ notification: Notification) {
        terminating = true
        if let p = server, p.isRunning {
            p.terminate()  // SIGTERM: uvicorn shuts down cleanly and stops Claude Code
            let deadline = Date().addingTimeInterval(4)
            while p.isRunning && Date() < deadline { usleep(100_000) }
            if p.isRunning { kill(p.processIdentifier, SIGKILL) }
        }
    }

    // MARK: window
    func buildWindow() {
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1180, height: 780),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered, defer: false)
        window.title = "Mike"
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.minSize = NSSize(width: 560, height: 520)
        window.isReleasedWhenClosed = false
        window.delegate = self
        // Solid, calm dark surface (matches the page, so there is no flash while it loads).
        window.isOpaque = true
        window.backgroundColor = AppDelegate.surfaceDark
        window.appearance = NSAppearance(named: .darkAqua)
        window.center()
        window.setFrameAutosaveName("MikeMainWindow")

        let config = WKWebViewConfiguration()
        config.mediaTypesRequiringUserActionForPlayback = []
        config.userContentController.add(self, name: "mike")
        config.preferences.setValue(true, forKey: "developerExtrasEnabled")
        // Keep listening (mic, timers) while the window is hidden or in the background.
        if #available(macOS 14.0, *) { config.preferences.inactiveSchedulingPolicy = .none }
        web = WKWebView(frame: .zero, configuration: config)
        web.uiDelegate = self
        web.navigationDelegate = self
        web.setValue(false, forKey: "drawsBackground")
        if #available(macOS 13.3, *) { web.isInspectable = true }

        let container = NSView()
        web.translatesAutoresizingMaskIntoConstraints = false
        dragStrip.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(web)
        container.addSubview(dragStrip)
        NSLayoutConstraint.activate([
            web.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            web.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            web.topAnchor.constraint(equalTo: container.topAnchor),
            web.bottomAnchor.constraint(equalTo: container.bottomAnchor),
            dragStrip.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            dragStrip.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            dragStrip.topAnchor.constraint(equalTo: container.topAnchor),
            dragStrip.heightAnchor.constraint(equalToConstant: 52),
        ])
        window.contentView = container
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        // Closing hides the window; Mike keeps listening through the pet and the menu bar.
        setGhost(true)
        return false
    }

    /// Ghost mode: the window stays on screen (the web engine only runs the mic and camera for
    /// on-screen windows) but is fully invisible, click-through, and out of Mission Control.
    func setGhost(_ on: Bool) {
        // Not fully transparent and not buried: macOS must still count the window as visible.
        window.alphaValue = on ? 0.004 : 1
        window.ignoresMouseEvents = on
        window.hasShadow = !on
        window.collectionBehavior = on ? [.stationary, .ignoresCycle, .canJoinAllSpaces] : [.managed]
        if on { window.level = .floating; window.orderFrontRegardless() }
        else { window.level = .normal }
    }

    func showWindow() {
        setGhost(false)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func showLoading(_ message: String) {
        // Quiet boot screen in the same dark surface as the app.
        let html = """
        <html><head><meta charset="utf-8"><style>
        html,body{margin:0;height:100%;background:#262624;color:#f5f4ef;font:14px -apple-system,system-ui;-webkit-font-smoothing:antialiased}
        body{display:grid;place-items:center}.w{text-align:center}
        .m{width:34px;height:34px;margin:0 auto 16px;display:flex;gap:4px;align-items:center;justify-content:center}
        .m i{width:4px;border-radius:2px;background:#d97757;animation:b 1.1s ease-in-out infinite}
        .m i:nth-child(1){height:12px}.m i:nth-child(2){height:22px;animation-delay:.12s}.m i:nth-child(3){height:16px;animation-delay:.24s}
        h1{font:500 22px "New York",ui-serif,Georgia,serif;letter-spacing:-.01em;margin:0}
        p{color:#9c9a92;margin:6px 0 0}@keyframes b{50%{transform:scaleY(.45)}}
        </style></head><body><div class="w"><div class="m"><i></i><i></i><i></i></div><h1>Mike</h1><p>\(message)</p></div></body></html>
        """
        web.loadHTMLString(html, baseURL: nil)
    }

    // MARK: backend
    func ensureServer() {
        isServerUp { up in
            if up { self.loadUI(); return }
            if self.server == nil { self.launchServer() }
            self.waitForServer(attempt: 0)
        }
    }

    func loadUI() {
        web.load(URLRequest(url: mikeURL))
        petController.load()
    }

    func isServerUp(_ done: @escaping (Bool) -> Void) {
        var req = URLRequest(url: mikeURL)
        req.timeoutInterval = 1
        URLSession.shared.dataTask(with: req) { _, resp, _ in
            let ok = (resp as? HTTPURLResponse)?.statusCode == 200
            DispatchQueue.main.async { done(ok) }
        }.resume()
    }

    func launchServer() {
        let python = mikeHome + "/.venv/bin/python"
        guard FileManager.default.isExecutableFile(atPath: python) else {
            showLoading("Couldn't find Mike's backend at \(mikeHome). Run ./run.sh once in Terminal to set it up.")
            return
        }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = ["server.py"]
        p.currentDirectoryURL = URL(fileURLWithPath: mikeHome)
        var env = ProcessInfo.processInfo.environment
        let home = NSHomeDirectory()
        env["PATH"] = "\(home)/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        env["PYTHONUNBUFFERED"] = "1"
        p.environment = env
        let logPath = mikeHome + "/data/server.log"
        // Keep the log small: past 2 MB, the old one becomes server.log.1 and a fresh one starts.
        if let size = (try? FileManager.default.attributesOfItem(atPath: logPath))?[.size] as? Int, size > 2_000_000 {
            try? FileManager.default.removeItem(atPath: logPath + ".1")
            try? FileManager.default.moveItem(atPath: logPath, toPath: logPath + ".1")
        }
        if !FileManager.default.fileExists(atPath: logPath) {
            FileManager.default.createFile(atPath: logPath, contents: nil)
        }
        if let log = FileHandle(forWritingAtPath: logPath) {
            log.seekToEndOfFile()
            p.standardOutput = log
            p.standardError = log
        }
        p.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async {
                guard let self, !self.terminating else { return }
                self.server = nil
                self.updateStatus(state: "offline")
                // The page reconnects by itself; bring the backend back after a crash.
                DispatchQueue.main.asyncAfter(deadline: .now() + 2) { self.ensureServer() }
            }
        }
        do {
            try p.run()
            server = p
        } catch {
            showLoading("Couldn't start Mike's backend: \(error.localizedDescription)")
        }
    }

    func waitForServer(attempt: Int) {
        isServerUp { up in
            if up {
                self.loadUI()
            } else if attempt > 240 {
                self.showLoading("Mike's backend didn't start. See ~/mike/data/server.log")
            } else {
                if attempt == 6 { self.showLoading("Loading voice and speech models…") }
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { self.waitForServer(attempt: attempt + 1) }
            }
        }
    }

    // MARK: web view
    @available(macOS 12.0, *)
    func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                 initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType,
                 decisionHandler: @escaping (WKPermissionDecision) -> Void)
    {
        // Only Mike's own local page may use the mic and camera (macOS still asks you once).
        decisionHandler(origin.host == "127.0.0.1" && origin.port == mikePort ? .grant : .deny)
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void)
    {
        if action.navigationType == .linkActivated, let url = action.request.url,
           url.host != "127.0.0.1", url.scheme == "http" || url.scheme == "https"
        {
            NSWorkspace.shared.open(url)  // links open in your browser
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView?
    {
        if let url = action.request.url { NSWorkspace.shared.open(url) }
        return nil
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { ensureServer() }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { retryLoad() }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        retryLoad()
    }
    func retryLoad() {
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { self.ensureServer() }
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any], let type = body["type"] as? String else { return }
        switch type {
        case "state": updateStatus(state: body["state"] as? String ?? "idle")
        case "mic":
            micOn = body["on"] as? Bool ?? false
            updateStatus(state: lastState)
        case "attention":
            // Mike needs a yes/no: the pet asks (voice, or right-click the pet). No window pops up.
            petController.summon()
        case "screenshot":
            let id = body["id"] as? String ?? ""
            captureScreen { b64 in
                self.js("window.mike && window.mike.screenshotResult('\(id)', \(b64.map { "'\($0)'" } ?? "null"))")
            }
        case "quit":
            quitCompletely()
        case "show":
            showWindow()
        case "hide":
            setGhost(true)
        case "theme":
            // The page tells us its theme so the title bar and window edges match it.
            let dark = body["dark"] as? Bool ?? true
            window.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
            window.backgroundColor = dark ? AppDelegate.surfaceDark : AppDelegate.surfaceLight
            surfaceIsDark = dark
        case "native":
            // Computer control (see Control.swift): result goes back as a JSON object literal.
            let id = body["id"] as? String ?? ""
            Control.shared.run(body["op"] as? String ?? "", body["args"] as? [String: Any] ?? [:]) { out in
                let data = (try? JSONSerialization.data(withJSONObject: out)) ?? Data("{}".utf8)
                self.js("window.mike && window.mike.nativeResult('\(id)', \(String(decoding: data, as: UTF8.self)))")
            }
        case "dragHoles":
            let rects = (body["rects"] as? [[Double]]) ?? []
            dragStrip.holes = rects.compactMap { $0.count == 4 ? NSRect(x: $0[0], y: $0[1], width: $0[2], height: $0[3]) : nil }
        default: break
        }
    }

    func js(_ code: String) { web.evaluateJavaScript(code, completionHandler: nil) }

    /// Capture the main display from inside Mike.app itself, so the Screen Recording permission
    /// you grant to "Mike" is exactly the one being used. Image size = screen points (click coordinates).
    func captureScreen(_ done: @escaping (String?) -> Void) {
        guard CGPreflightScreenCaptureAccess() else {
            CGRequestScreenCaptureAccess()   // shows the macOS prompt once and adds Mike to the list
            done(nil)
            return
        }
        Task {
            var result: String? = nil
            do {
                let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
                let main = CGMainDisplayID()
                if let display = content.displays.first(where: { $0.displayID == main }) ?? content.displays.first {
                    let mine = content.windows.filter { $0.owningApplication?.processID == ProcessInfo.processInfo.processIdentifier }
                    let filter = SCContentFilter(display: display, excludingWindows: mine)   // don't photograph Mike itself
                    let cfg = SCStreamConfiguration()
                    cfg.width = display.width
                    cfg.height = display.height
                    cfg.showsCursor = true
                    let img = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: cfg)
                    result = NSBitmapImageRep(cgImage: img).representation(using: .jpeg, properties: [.compressionFactor: 0.8])?.base64EncodedString()
                }
            } catch { NSLog("Mike: screen capture failed: \(error)") }
            let out = result
            DispatchQueue.main.async { done(out) }
        }
    }

    // MARK: menu bar
    func buildStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        let menu = NSMenu()
        stateItem = NSMenuItem(title: "Mike is starting…", action: nil, keyEquivalent: "")
        stateItem.isEnabled = false
        menu.addItem(stateItem)
        menu.addItem(.separator())
        let talk = NSMenuItem(title: "Talk to Mike", action: #selector(talkAction), keyEquivalent: " ")
        talk.keyEquivalentModifierMask = [.option]
        talk.target = self
        menu.addItem(talk)
        let show = NSMenuItem(title: "Show Mike", action: #selector(showAction), keyEquivalent: "")
        show.target = self
        menu.addItem(show)
        micItem = NSMenuItem(title: "Stop Listening", action: #selector(toggleMicAction), keyEquivalent: "m")
        micItem.keyEquivalentModifierMask = [.option, .shift]
        micItem.target = self
        menu.addItem(micItem)
        menu.addItem(.separator())
        let quit = NSMenuItem(title: "Quit Mike", action: #selector(AppDelegate.quitCompletely), keyEquivalent: "q")
        quit.keyEquivalentModifierMask = [.option, .shift]
        quit.target = sharedDelegate
        menu.addItem(quit)
        menu.delegate = self  // pet items are rebuilt each time the menu opens
        statusItem.menu = menu
        updateStatus(state: "offline")
    }

    func updateStatus(state: String) {
        lastState = state
        let (symbol, label): (String, String) = {
            if !micOn && (state == "idle" || state == "listening") { return ("mic.slash", "Mic is off") }
            switch state {
            case "listening": return ("waveform", "Listening")
            case "thinking": return ("ellipsis.circle", "Thinking")
            case "speaking": return ("speaker.wave.2", "Speaking")
            case "confirming": return ("exclamationmark.circle", "Waiting for your OK")
            case "offline": return ("circle.dotted", "Starting up")
            case "elsewhere": return ("circle.dashed", "Open in another window")
            default: return ("waveform.circle", "Ready — say “Mike” or press ⌥Space")
            }
        }()
        let img = NSImage(systemSymbolName: symbol, accessibilityDescription: "Mike: \(label)")
        img?.isTemplate = true
        statusItem.button?.image = img
        statusItem.button?.toolTip = "Mike — \(label)"
        stateItem.title = label
        micItem.title = micOn ? "Stop Listening" : "Start Listening"
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        // Replace the pet section (tagged 77) with fresh checkmarks.
        menu.items.filter { $0.tag == 77 }.forEach(menu.removeItem)
        let insertAt = (menu.items.firstIndex(where: { $0.title == "Quit Mike" }) ?? menu.items.count) - 1
        let temp = NSMenu()
        let summon = NSMenuItem(title: "Show Pet Now", action: #selector(summonPet), keyEquivalent: "")
        summon.target = self
        temp.addItem(summon)
        petController.addPetItems(to: temp)
        temp.addItem(.separator())
        for (i, item) in temp.items.enumerated() {
            temp.removeItem(item)
            item.tag = 77
            menu.insertItem(item, at: insertAt + 1 + i)
        }
    }

    @objc func summonPet() { petController.summon() }
    @objc func talkAction() { js("window.mike && window.mike.wake()") }
    @objc func showAction() { showWindow() }
    @objc func toggleMicAction() { js("window.mike && window.mike.toggleMic()") }
    @objc func reloadAction() { ensureServer() }

    // MARK: ⌥Space hotkey (works from any app)
    func registerHotKey() {
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, event, _ in
            var hk = EventHotKeyID()
            GetEventParameter(event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
                              nil, MemoryLayout<EventHotKeyID>.size, nil, &hk)
            DispatchQueue.main.async {
                if hk.id == 2 { sharedDelegate?.quitCompletely() }
                else if hk.id == 3 { sharedDelegate?.micHotKey() }
                else { sharedDelegate?.hotKeyPressed() }
            }
            return noErr
        }, 1, &spec, nil, nil)
        let id = EventHotKeyID(signature: OSType(0x4D49_4B45), id: 1)  // 'MIKE'
        let status = RegisterEventHotKey(UInt32(kVK_Space), UInt32(optionKey), id, GetApplicationEventTarget(), 0, &hotKeyRef)
        if status != noErr { NSLog("Mike: couldn't register ⌥Space (another app may use it): \(status)") }
        // ⌥⇧Q quits Mike from anywhere.
        let quitID = EventHotKeyID(signature: OSType(0x4D49_4B45), id: 2)
        let qs = RegisterEventHotKey(UInt32(kVK_ANSI_Q), UInt32(optionKey | shiftKey), quitID, GetApplicationEventTarget(), 0, &quitHotKeyRef)
        if qs != noErr { NSLog("Mike: couldn't register ⌥⇧Q: \(qs)") }
        // ⌥⇧M turns the microphone off / on from anywhere.
        let micID = EventHotKeyID(signature: OSType(0x4D49_4B45), id: 3)
        let ms = RegisterEventHotKey(UInt32(kVK_ANSI_M), UInt32(optionKey | shiftKey), micID, GetApplicationEventTarget(), 0, &micHotKeyRef)
        if ms != noErr { NSLog("Mike: couldn't register ⌥⇧M: \(ms)") }
    }

    /// ⌥⇧M: mute or unmute, with a soft sound so you know which without looking.
    func micHotKey() {
        let wasOn = micOn
        toggleMicAction()
        NSSound(named: wasOn ? "Pop" : "Tink")?.play()
    }

    func hotKeyPressed() {
        // Talk without switching apps. If Mike is busy, this interrupts instead.
        js("window.mike && window.mike.wake()")
    }

    // MARK: main menu
    func buildMainMenu() {
        let main = NSMenu()

        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About Mike", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide Mike", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        let others = appMenu.addItem(withTitle: "Hide Others", action: #selector(NSApplication.hideOtherApplications(_:)), keyEquivalent: "h")
        others.keyEquivalentModifierMask = [.command, .option]
        appMenu.addItem(.separator())
        // ⌘Q closes the window but Mike keeps listening; quitting for real is ⌥⇧Q.
        appMenu.addItem(withTitle: "Close Window (Mike keeps listening)", action: #selector(AppDelegate.closeToBackground), keyEquivalent: "q").target = self
        let off = appMenu.addItem(withTitle: "Quit Mike", action: #selector(AppDelegate.quitCompletely), keyEquivalent: "q")
        off.keyEquivalentModifierMask = [.option, .shift]
        off.target = self
        appItem.submenu = appMenu
        main.addItem(appItem)

        let editItem = NSMenuItem()
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        let redo = edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        main.addItem(editItem)

        let viewItem = NSMenuItem()
        let view = NSMenu(title: "View")
        let reload = view.addItem(withTitle: "Reload", action: #selector(reloadAction), keyEquivalent: "r")
        reload.target = self
        viewItem.submenu = view
        main.addItem(viewItem)

        let winItem = NSMenuItem()
        let win = NSMenu(title: "Window")
        win.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        win.addItem(withTitle: "Zoom", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        win.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        winItem.submenu = win
        main.addItem(winItem)
        NSApp.windowsMenu = win

        NSApp.mainMenu = main
    }
}

// MARK: - Desktop pet

let petChoices: [(id: String, name: String)] = [
    ("mike", "Mike"), ("mochi", "Mochi"), ("tapey", "Tapey"), ("bolt", "Bolt"), ("boo", "Boo"), ("kit", "Kit"), ("none", "None"),
]
let petModes: [(id: String, name: String)] = [
    ("active", "When Mike Is Active"), ("always", "Always"), ("off", "Never"),
]

/// Sits on top of the pet's web view: only the sprite is clickable, the rest of the window passes clicks through.
final class PetMouseView: NSView {
    weak var controller: PetController?
    private var downAt = NSPoint.zero
    private var dragged = false
    private var pendingClick: DispatchWorkItem?

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func hitTest(_ point: NSPoint) -> NSView? {
        guard let c = controller else { return nil }
        return c.spriteRect.contains(convert(point, from: superview)) ? self : nil
    }
    override func mouseDown(with event: NSEvent) {
        downAt = NSEvent.mouseLocation
        dragged = false
        controller?.beginDrag()
    }
    override func mouseDragged(with event: NSEvent) {
        let p = NSEvent.mouseLocation
        if !dragged && hypot(p.x - downAt.x, p.y - downAt.y) > 3 { dragged = true }
        if dragged { controller?.drag(dx: p.x - downAt.x, dy: p.y - downAt.y) }
    }
    override func mouseUp(with event: NSEvent) {
        if dragged { controller?.endDrag(); return }
        if event.clickCount >= 2 {
            pendingClick?.cancel()
            controller?.doubleClick()
        } else {
            // Wait briefly so a double-click doesn't also count as a click.
            let work = DispatchWorkItem { [weak self] in self?.controller?.click() }
            pendingClick = work
            DispatchQueue.main.asyncAfter(deadline: .now() + NSEvent.doubleClickInterval, execute: work)
        }
    }
    override func rightMouseDown(with event: NSEvent) {
        guard let menu = controller?.makeMenu() else { return }
        NSMenu.popUpContextMenu(menu, with: event, for: self)
    }
}

final class PetController: NSObject, WKScriptMessageHandler, WKNavigationDelegate {
    let size = NSSize(width: 260, height: 290)
    var panel: NSPanel!
    var web: WKWebView!
    weak var app: AppDelegate?
    var pet = "mike"
    var mode = "active"
    var visible = false
    var wanderOn = false
    var dragging = false
    var dragStart = NSPoint.zero
    var target: NSPoint?
    var nextWanderAt = Date()
    var facing: CGFloat = 1
    var lastLook = (dx: 9.0, dy: 9.0)
    var timer: Timer?
    var loaded = false
    var confirmPending = false

    /// The sprite's area inside the panel (AppKit coordinates, origin bottom-left).
    var spriteRect: NSRect { NSRect(x: (size.width - 130) / 2, y: 6, width: 130, height: 128) }

    var home: NSPoint {
        get {
            let d = UserDefaults.standard
            if d.object(forKey: "petHomeX") != nil {
                return clamp(NSPoint(x: d.double(forKey: "petHomeX"), y: d.double(forKey: "petHomeY")))
            }
            let f = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
            return NSPoint(x: f.maxX - size.width - 40, y: f.minY + 8)
        }
        set {
            UserDefaults.standard.set(newValue.x, forKey: "petHomeX")
            UserDefaults.standard.set(newValue.y, forKey: "petHomeY")
        }
    }

    func setup(app: AppDelegate) {
        self.app = app
        panel = NSPanel(contentRect: NSRect(origin: home, size: size),
                        styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false

        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "pet")
        config.mediaTypesRequiringUserActionForPlayback = []
        if #available(macOS 14.0, *) { config.preferences.inactiveSchedulingPolicy = .none }
        web = WKWebView(frame: NSRect(origin: .zero, size: size), configuration: config)
        web.setValue(false, forKey: "drawsBackground")
        web.navigationDelegate = self
        web.autoresizingMask = [.width, .height]

        let root = NSView(frame: NSRect(origin: .zero, size: size))
        root.addSubview(web)
        let mouse = PetMouseView(frame: root.bounds)
        mouse.autoresizingMask = [.width, .height]
        mouse.controller = self
        root.addSubview(mouse)
        panel.contentView = root

        timer = Timer.scheduledTimer(withTimeInterval: 1.0 / 30, repeats: true) { [weak self] _ in self?.tick() }
        RunLoop.main.add(timer!, forMode: .common)
    }

    func load() {
        guard !loaded else { return }
        loaded = true
        web.load(URLRequest(url: mikeURL.appendingPathComponent("static/pet.html")))
    }

    func js(_ code: String) { web.evaluateJavaScript(code, completionHandler: nil) }

    // MARK: messages from pet.js
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any], let type = body["type"] as? String else { return }
        switch type {
        case "visible": setVisible(body["on"] as? Bool ?? false)
        case "wander": wanderOn = body["on"] as? Bool ?? false
        case "pet":
            pet = body["pet"] as? String ?? pet
            mode = body["mode"] as? String ?? mode
        case "confirm": confirmPending = body["on"] as? Bool ?? false
        default: break
        }
    }

    func setVisible(_ on: Bool) {
        guard on != visible else { return }
        visible = on
        if on {
            if !panel.isVisible { panel.setFrameOrigin(home); target = nil }
            panel.orderFrontRegardless()
        } else {
            panel.orderOut(nil)
        }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        loaded = false
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { self.load() }
    }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { loaded = false; load() }

    // MARK: per-frame: click-through, eye tracking, wandering
    func tick() {
        guard visible else { return }
        let m = NSEvent.mouseLocation
        let f = panel.frame
        let sprite = spriteRect.offsetBy(dx: f.minX, dy: f.minY)
        panel.ignoresMouseEvents = !sprite.contains(m) && !dragging

        // Eyes follow the cursor (−1…1, y down for the page).
        let cx = sprite.midX, cy = sprite.midY + 20
        let dx = Double(max(-1, min(1, (m.x - cx) / 220)) * facing)
        let dy = Double(max(-1, min(1, -(m.y - cy) / 220)))
        if abs(dx - lastLook.dx) > 0.08 || abs(dy - lastLook.dy) > 0.08 {
            lastLook = (dx, dy)
            js("window.pet && pet.look(\(dx),\(dy))")
        }

        guard wanderOn, !dragging else { return }
        let now = Date()
        if target == nil || now > nextWanderAt {
            let h = home
            let tx = h.x + CGFloat.random(in: -170...40)
            let ty = h.y + CGFloat.random(in: 0...70)
            target = clamp(NSPoint(x: tx, y: ty))
            nextWanderAt = now.addingTimeInterval(Double.random(in: 4...8))
        }
        guard let t = target else { return }
        let ddx = t.x - f.minX, ddy = t.y - f.minY
        let dist = hypot(ddx, ddy)
        if dist < 1 { return }
        let step = min(dist, max(0.6, dist * 0.04))  // ease in, glide out
        panel.setFrameOrigin(NSPoint(x: f.minX + ddx / dist * step, y: f.minY + ddy / dist * step))
        let dir: CGFloat = ddx < -2 ? -1 : (ddx > 2 ? 1 : facing)
        if dir != facing { facing = dir; js("window.pet && pet.face(\(Int(dir)))") }
    }

    func clamp(_ p: NSPoint) -> NSPoint {
        let screens = NSScreen.screens.map(\.visibleFrame)
        let f = screens.first(where: { $0.contains(NSPoint(x: p.x + size.width / 2, y: p.y + 40)) })
            ?? NSScreen.main?.visibleFrame ?? .zero
        return NSPoint(x: min(max(p.x, f.minX - 40), f.maxX - size.width + 40), y: min(max(p.y, f.minY), f.maxY - size.height))
    }

    // MARK: mouse
    func beginDrag() { dragging = true; dragStart = panel.frame.origin }
    func drag(dx: CGFloat, dy: CGFloat) { panel.setFrameOrigin(NSPoint(x: dragStart.x + dx, y: dragStart.y + dy)) }
    func endDrag() {
        dragging = false
        home = panel.frame.origin  // remember where you put it
        target = home
        nextWanderAt = Date().addingTimeInterval(6)
    }
    func click() { js("window.pet && pet.click()") }
    func doubleClick() { app?.showWindow() }

    // MARK: menus
    func makeMenu() -> NSMenu {
        let menu = NSMenu()
        if confirmPending {
            let yes = NSMenuItem(title: "Allow", action: #selector(allowAction), keyEquivalent: "")
            yes.target = self
            menu.addItem(yes)
            let no = NSMenuItem(title: "Don't Allow", action: #selector(denyAction), keyEquivalent: "")
            no.target = self
            menu.addItem(no)
            menu.addItem(.separator())
        }
        let talk = NSMenuItem(title: "Talk to Mike", action: #selector(talkAction), keyEquivalent: "")
        talk.target = self
        menu.addItem(talk)
        let open = NSMenuItem(title: "Open Mike", action: #selector(openAction), keyEquivalent: "")
        open.target = self
        menu.addItem(open)
        let mic = NSMenuItem(title: (sharedDelegate?.micOn ?? true) ? "Stop Listening" : "Start Listening",
                             action: #selector(AppDelegate.toggleMicAction), keyEquivalent: "m")
        mic.keyEquivalentModifierMask = [.option, .shift]
        mic.target = sharedDelegate
        menu.addItem(mic)
        menu.addItem(.separator())
        addPetItems(to: menu)
        menu.addItem(.separator())
        let quit = NSMenuItem(title: "Quit Mike", action: #selector(AppDelegate.quitCompletely), keyEquivalent: "q")
        quit.keyEquivalentModifierMask = [.option, .shift]
        quit.target = sharedDelegate
        menu.addItem(quit)
        return menu
    }

    func addPetItems(to menu: NSMenu) {
        let pets = NSMenuItem(title: "Pet", action: nil, keyEquivalent: "")
        let petMenu = NSMenu()
        for p in petChoices {
            let item = NSMenuItem(title: p.name, action: #selector(choosePet(_:)), keyEquivalent: "")
            item.representedObject = p.id
            item.target = self
            item.state = p.id == pet ? .on : .off
            petMenu.addItem(item)
        }
        pets.submenu = petMenu
        menu.addItem(pets)
        let show = NSMenuItem(title: "Show Pet", action: nil, keyEquivalent: "")
        let showMenu = NSMenu()
        for m in petModes {
            let item = NSMenuItem(title: m.name, action: #selector(chooseMode(_:)), keyEquivalent: "")
            item.representedObject = m.id
            item.target = self
            item.state = m.id == mode ? .on : .off
            showMenu.addItem(item)
        }
        show.submenu = showMenu
        menu.addItem(show)
    }

    @objc func allowAction() { js("window.pet && pet.answer(true)") }
    @objc func denyAction() { js("window.pet && pet.answer(false)") }
    @objc func talkAction() { app?.talkAction() }
    @objc func openAction() { app?.showWindow() }
    @objc func choosePet(_ sender: NSMenuItem) {
        guard let id = sender.representedObject as? String else { return }
        js("window.pet && pet.setPet('\(id)')")
    }
    @objc func chooseMode(_ sender: NSMenuItem) {
        guard let id = sender.representedObject as? String else { return }
        js("window.pet && pet.setMode('\(id)')")
    }
    func summon() { js("window.pet && pet.summon(8000)") }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
