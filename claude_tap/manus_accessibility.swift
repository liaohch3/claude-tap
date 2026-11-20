// Read the current Manus Studio conversation using the macOS Accessibility API.
// The default JSON snapshot contains task text. --diagnose emits only status and counts.
import AppKit
import ApplicationServices
import Foundation

private let studioBundleIdentifier = "im.manus.desktop"
private let webAreaRole = "AXWebArea"
private let messageListClass = "pb-[var(--chat-message-list-padding-bottom)]"
private let maximumDepth = 96
private let maximumNodes = 8_000
private let maximumTextBytes = 2 * 1024 * 1024

private struct Node: Encodable {
    let role: String
    let subrole: String?
    let identifier: String?
    let domId: String?
    let domClasses: [String]?
    let title: String?
    let description: String?
    let value: String?
    let children: [Node]?
}

private struct Snapshot: Encodable {
    let status: String
    let taskId: String?
    let title: String?
    let isRunning: Bool?
    let taskStatus: String?
    let conversation: Node?
    let diagnostics: [String: Int]?

    init(
        _ status: String,
        taskId: String? = nil,
        title: String? = nil,
        isRunning: Bool? = nil,
        taskStatus: String? = nil,
        conversation: Node? = nil,
        diagnostics: [String: Int]? = nil
    ) {
        self.status = status
        self.taskId = taskId
        self.title = title
        self.isRunning = isRunning
        self.taskStatus = taskStatus
        self.conversation = conversation
        self.diagnostics = diagnostics
    }
}

private func attribute(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    let result = AXUIElementCopyAttributeValue(element, name as CFString, &value)
    return result == .success ? value : nil
}

private func stringAttribute(_ element: AXUIElement, _ name: String) -> String? {
    guard let value = attribute(element, name) as? String, !value.isEmpty else { return nil }
    return value
}

private func asElement(_ value: CFTypeRef?) -> AXUIElement? {
    guard let value, CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
    return unsafeBitCast(value, to: AXUIElement.self)
}

private func children(_ element: AXUIElement) -> [AXUIElement]? {
    var value: CFTypeRef?
    switch AXUIElementCopyAttributeValue(element, kAXChildrenAttribute as CFString, &value) {
    case .success:
        return value as? [AXUIElement]
    case .noValue, .attributeUnsupported:
        return []
    default:
        return nil
    }
}

private func domClasses(_ element: AXUIElement) -> [String] {
    return attribute(element, "AXDOMClassList") as? [String] ?? []
}

private func taskId(_ webArea: AXUIElement) -> String? {
    let rawURL = attribute(webArea, kAXURLAttribute)
    guard let url = (rawURL as? URL) ?? (rawURL as? String).flatMap(URL.init(string:)),
          url.scheme == "app", url.host == "manus", url.user == nil, url.password == nil, url.port == nil,
          url.path.hasPrefix("/app/") else { return nil }
    let id = String(url.path.dropFirst(5))
    let isAlphanumeric = id.utf8.allSatisfy { (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) }
    return id.utf8.count == 22 && isAlphanumeric ? id : nil
}

// Search layout metadata only. A matched subtree or nested web area is not traversed.
private func findElements(
    below root: AXUIElement,
    matching matches: (AXUIElement, String) -> Bool
) -> [AXUIElement]? {
    guard let initial = children(root) else { return nil }
    var stack = initial.reversed().map { ($0, 1) }
    var result: [AXUIElement] = []
    var visited = 0
    while let (element, depth) = stack.popLast() {
        visited += 1
        guard visited <= maximumNodes, depth <= maximumDepth else { return nil }
        guard let role = stringAttribute(element, kAXRoleAttribute) else { return nil }
        if matches(element, role) {
            result.append(element)
        } else if role != webAreaRole {
            guard let descendants = children(element) else { return nil }
            stack.append(contentsOf: descendants.reversed().map { ($0, depth + 1) })
        }
    }
    return result
}

private struct ConversationReader {
    let diagnose: Bool
    var nodeCount = 0
    var staticTextCount = 0
    var textBytes = 0
    var incomplete = false
    var isThinking = false

    mutating func read(_ element: AXUIElement, depth: Int = 0, outsideMessage: Bool = true) -> Node? {
        guard !incomplete else { return nil }
        guard nodeCount < maximumNodes, depth <= maximumDepth,
              let role = stringAttribute(element, kAXRoleAttribute) else {
            incomplete = true
            return nil
        }
        // Exclude nested web views and editable fields even inside a task's message list.
        if [webAreaRole, kAXTextAreaRole, kAXTextFieldRole, kAXComboBoxRole].contains(role) {
            return nil
        }

        nodeCount += 1
        if role == kAXStaticTextRole { staticTextCount += 1 }
        guard let descendants = children(element) else {
            incomplete = true
            return nil
        }

        let classes = domClasses(element)
        let isMessage = classes.contains("user-message-bubble-new") || classes.contains("chat-message-body")
            || (classes.contains("whitespace-pre-wrap") && classes.contains("u-break-words"))
        let canShowStatus = outsideMessage && !isMessage
        // This styled status chip is separate from the user and assistant message bodies.
        if canShowStatus && role == kAXGroupRole && classes.contains("shimmer-text-secondary") {
            isThinking = isThinking || descendants.contains { child in
                guard stringAttribute(child, kAXRoleAttribute) == kAXStaticTextRole,
                      let label = stringAttribute(child, kAXValueAttribute) else { return false }
                let normalized = label.trimmingCharacters(in: CharacterSet.whitespacesAndNewlines.union(
                    CharacterSet(charactersIn: ".…")
                )).lowercased()
                return normalized == "思考中" || normalized == "thinking"
            }
        }
        let subrole = diagnose ? nil : stringAttribute(element, kAXSubroleAttribute)
        let identifier = diagnose ? nil : stringAttribute(element, kAXIdentifierAttribute)
        let domId = diagnose ? nil : stringAttribute(element, "AXDOMIdentifier")
        let title = diagnose ? nil : stringAttribute(element, kAXTitleAttribute)
        let description = diagnose ? nil : stringAttribute(element, kAXDescriptionAttribute)
        let value = diagnose ? nil : stringAttribute(element, kAXValueAttribute)
        textBytes += classes.reduce(0) { $0 + $1.utf8.count }
        textBytes += [subrole, identifier, domId, title, description, value].compactMap { $0 }.reduce(0) {
            $0 + $1.utf8.count
        }
        guard textBytes <= maximumTextBytes else {
            incomplete = true
            return nil
        }

        var nodes: [Node] = []
        for child in descendants {
            if let node = read(child, depth: depth + 1, outsideMessage: canShowStatus) { nodes.append(node) }
            if incomplete { return nil }
        }
        return Node(
            role: role, subrole: subrole, identifier: identifier, domId: domId,
            domClasses: diagnose || classes.isEmpty ? nil : classes, title: title, description: description,
            value: value, children: nodes.isEmpty ? nil : nodes
        )
    }
}

private func completionStatusVisible(among boxChildren: [AXUIElement]) -> Bool {
    let composers = boxChildren.filter {
        stringAttribute($0, kAXRoleAttribute) == kAXGroupRole && domClasses($0).contains("group/chat-input")
    }
    guard composers.count == 1, let composer = composers.first, let composerChildren = children(composer) else {
        return false
    }
    let statusCards = composerChildren.filter {
        stringAttribute($0, kAXRoleAttribute) == kAXGroupRole && domClasses($0).contains("rounded-t-[22px]")
    }
    guard statusCards.count == 1, let card = statusCards.first, let labels = children(card) else { return false }
    // Read only the completion card's immediate text label. The editor is a separate sibling.
    return labels.contains { label in
        guard stringAttribute(label, kAXRoleAttribute) == kAXStaticTextRole else { return false }
        return [kAXValueAttribute, kAXTitleAttribute, kAXDescriptionAttribute].contains { name in
            let text = stringAttribute(label, name)?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
            return text == "任务已完成" || text == "任务完成" || text == "task completed" || text == "task complete"
        }
    }
}

private func readSnapshot(diagnose: Bool) -> Snapshot {
    guard AXIsProcessTrusted() else { return Snapshot("permission_denied") }
    let apps = NSRunningApplication.runningApplications(withBundleIdentifier: studioBundleIdentifier)
    guard let running = apps.first(where: \.isActive) ?? apps.first else { return Snapshot("app_not_running") }
    let app = AXUIElementCreateApplication(running.processIdentifier)
    // Bound AX calls if Studio closes or stops responding while reading its tree.
    AXUIElementSetMessagingTimeout(app, 0.5)
    guard let window = asElement(attribute(app, kAXFocusedWindowAttribute))
            ?? asElement(attribute(app, kAXMainWindowAttribute)) else { return Snapshot("no_task") }

    guard let taskAreas = findElements(below: window, matching: { element, role in
        role == webAreaRole && taskId(element) != nil
    }) else { return Snapshot("snapshot_incomplete") }
    guard taskAreas.count == 1, let webArea = taskAreas.first, let id = taskId(webArea) else {
        return Snapshot("no_task", diagnostics: diagnose ? ["task_web_areas": taskAreas.count] : nil)
    }

    guard let viewports = findElements(below: webArea, matching: { element, _ in
        stringAttribute(element, "AXDOMIdentifier") == "manus-chat-scroll-viewport"
    }), viewports.count == 1, let viewport = viewports.first,
          let boxes = findElements(below: viewport, matching: { element, _ in
              stringAttribute(element, "AXDOMIdentifier") == "manus-chat-box"
          }), boxes.count == 1, let box = boxes.first,
          let boxChildren = children(box) else { return Snapshot("conversation_unavailable") }
    let lists = boxChildren.filter { domClasses($0).contains(messageListClass) }
    guard lists.count == 1, let list = lists.first else { return Snapshot("conversation_unavailable") }

    let title = diagnose ? nil : stringAttribute(webArea, kAXTitleAttribute)
    var reader = ConversationReader(diagnose: diagnose)
    guard let conversation = reader.read(list), !reader.incomplete else {
        return Snapshot("snapshot_incomplete")
    }
    let completed = !reader.isThinking && completionStatusVisible(among: boxChildren)
    guard taskId(webArea) == id else { return Snapshot("snapshot_incomplete") }
    if diagnose {
        return Snapshot("ok", diagnostics: [
            "task_web_areas": taskAreas.count,
            "conversation_areas": lists.count,
            "conversation_nodes": reader.nodeCount,
            "static_text_nodes": reader.staticTextCount,
            "thinking_status_visible": reader.isThinking ? 1 : 0,
            "completion_status_visible": completed ? 1 : 0,
        ])
    }
    let isRunning: Bool? = reader.isThinking ? true : (completed ? false : nil)
    let taskStatus = reader.isThinking ? "thinking" : (completed ? "completed" : nil)
    return Snapshot(
        "ok", taskId: id, title: title, isRunning: isRunning, taskStatus: taskStatus, conversation: conversation
    )
}

let diagnosticsOnly = CommandLine.arguments.dropFirst().elementsEqual(["--diagnose"])
if CommandLine.arguments.count > 1 && !diagnosticsOnly {
    FileHandle.standardError.write(Data("Usage: manus-accessibility [--diagnose]\n".utf8))
    exit(2)
}

let encoder = JSONEncoder()
encoder.keyEncodingStrategy = .convertToSnakeCase
do {
    let snapshot = try encoder.encode(readSnapshot(diagnose: diagnosticsOnly))
    FileHandle.standardOutput.write(snapshot)
    FileHandle.standardOutput.write(Data([10]))
} catch {
    FileHandle.standardError.write(Data("Could not encode the Manus accessibility snapshot.\n".utf8))
    exit(1)
}
