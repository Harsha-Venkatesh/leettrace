import SwiftUI
import WebKit

@main
struct LeetTraceApp: App {
    var body: some Scene {
        WindowGroup("LeetTrace") {
            TracerWebView()
                .frame(minWidth: 1100, minHeight: 680)
        }
        .defaultSize(width: 1440, height: 900)
        .commands {
            CommandGroup(replacing: .newItem) {}
            CommandGroup(after: .toolbar) {
                Button("Zoom In") { Zoom.shared.step(+1) }
                    .keyboardShortcut("+", modifiers: .command)
                Button("Zoom Out") { Zoom.shared.step(-1) }
                    .keyboardShortcut("-", modifiers: .command)
                Button("Actual Size") { Zoom.shared.set(1.0) }
                    .keyboardShortcut("0", modifiers: .command)
                Divider()
            }
        }
    }
}

/// Scales the whole UI (text, panels, trees) via WKWebView page zoom; remembered across launches.
final class Zoom {
    static let shared = Zoom()
    static let levels: [CGFloat] = [0.67, 0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.4, 1.5, 1.75, 2.0, 2.5]
    weak var webView: WKWebView?

    var current: CGFloat {
        let v = UserDefaults.standard.double(forKey: "pageZoom")
        return v > 0 ? CGFloat(v) : 1.0
    }

    func step(_ dir: Int) {
        let cur = current
        let next = dir > 0
            ? Zoom.levels.first { $0 > cur + 0.001 } ?? Zoom.levels.last!
            : Zoom.levels.last { $0 < cur - 0.001 } ?? Zoom.levels.first!
        set(next)
    }

    func set(_ z: CGFloat) {
        UserDefaults.standard.set(Double(z), forKey: "pageZoom")
        apply()
    }

    func apply() {
        guard let web = webView else { return }
        web.pageZoom = current
        // let the page redraw its pointer arrows for the new scale
        web.evaluateJavaScript("window.dispatchEvent(new Event('resize'))")
    }
}

/// Hosts the bundled web UI and answers its `tracer` messages by running Python.
struct TracerWebView: NSViewRepresentable {
    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.userContentController.addScriptMessageHandler(TracerBridge(), contentWorld: .page, name: "tracer")
        config.userContentController.add(ZoomBridge(), name: "zoom")
        config.preferences.setValue(true, forKey: "developerExtrasEnabled")

        let web = WKWebView(frame: .zero, configuration: config)
        web.setValue(false, forKey: "drawsBackground")
        Zoom.shared.webView = web
        web.pageZoom = Zoom.shared.current
        if let dir = Bundle.main.resourceURL?.appendingPathComponent("web") {
            web.loadFileURL(dir.appendingPathComponent("index.html"), allowingReadAccessTo: dir)
        }
        return web
    }

    func updateNSView(_ nsView: WKWebView, context: Context) {}
}

/// Receives ⌘= (zoom in without Shift) from the page, since the menu item is bound to ⌘+.
final class ZoomBridge: NSObject, WKScriptMessageHandler {
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        if let dir = message.body as? Int { Zoom.shared.step(dir) }
    }
}

final class TracerBridge: NSObject, WKScriptMessageHandlerWithReply {
    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage,
                               replyHandler: @escaping (Any?, String?) -> Void) {
        let request = (message.body as? String) ?? "{}"
        DispatchQueue.global(qos: .userInitiated).async {
            let response = TracerBridge.run(request)
            DispatchQueue.main.async { replyHandler(response, nil) }
        }
    }

    /// Finds a Python 3 interpreter. Apps launched from Finder get a minimal PATH, so check the usual spots.
    static func pythonPath() -> String? {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let candidates = [
            ProcessInfo.processInfo.environment["LEETTRACE_PYTHON"],
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "\(home)/anaconda3/bin/python3",
            "\(home)/miniconda3/bin/python3",
            "\(home)/.pyenv/shims/python3",
            "/Library/Frameworks/Python.framework/Versions/Current/bin/python3",
            "/usr/bin/python3",
        ]
        return candidates.compactMap { $0 }.first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    static func errorJSON(_ msg: String) -> String {
        let payload: [String: Any] = ["ok": false, "error": ["msg": msg]]
        let data = try? JSONSerialization.data(withJSONObject: payload)
        return data.flatMap { String(data: $0, encoding: .utf8) } ?? "{\"ok\":false}"
    }

    static func run(_ request: String) -> String {
        guard let python = pythonPath() else {
            return errorJSON("Python 3 was not found. Install it (e.g. `brew install python`) or set LEETTRACE_PYTHON.")
        }
        guard let tracer = Bundle.main.path(forResource: "tracer", ofType: "py") else {
            return errorJSON("tracer.py is missing from the app bundle.")
        }

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: python)
        proc.arguments = ["-I", tracer]
        var env = ProcessInfo.processInfo.environment
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        proc.environment = env

        let stdin = Pipe(), stdout = Pipe(), stderr = Pipe()
        proc.standardInput = stdin
        proc.standardOutput = stdout
        proc.standardError = stderr

        var errData = Data()
        let errLock = NSLock()
        stderr.fileHandleForReading.readabilityHandler = { h in
            let chunk = h.availableData
            errLock.lock(); errData.append(chunk); errLock.unlock()
        }

        do { try proc.run() } catch {
            return errorJSON("Could not start Python at \(python): \(error.localizedDescription)")
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + 25) {
            if proc.isRunning { proc.terminate() }
        }
        DispatchQueue.global().async {
            stdin.fileHandleForWriting.write(request.data(using: .utf8) ?? Data())
            try? stdin.fileHandleForWriting.close()
        }

        let out = stdout.fileHandleForReading.readDataToEndOfFile()
        proc.waitUntilExit()
        stderr.fileHandleForReading.readabilityHandler = nil

        if let text = String(data: out, encoding: .utf8), text.hasPrefix("{") {
            return text
        }
        errLock.lock()
        let errText = String(data: errData, encoding: .utf8) ?? ""
        errLock.unlock()
        let tail = errText.split(separator: "\n").suffix(4).joined(separator: "\n")
        return errorJSON(proc.terminationReason == .uncaughtSignal
                         ? "Python was stopped (took longer than 25 s)."
                         : "Python failed: \(tail.isEmpty ? "no output" : tail)")
    }
}
