import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
#if canImport(Darwin)
import Darwin
#else
import Glibc
#endif

private enum TestFailure: Error {
    case failed(String)
}

private func check(_ condition: @autoclosure () throws -> Bool, _ name: String) throws {
    let passed = try condition()
    if !passed {
        throw TestFailure.failed(name)
    }
}

private final class StubURLProtocol: URLProtocol, @unchecked Sendable {
    static var handler: ((StubURLProtocol) -> Void)?
    static var started = 0
    static let lock = NSLock()

    override class func canInit(with request: URLRequest) -> Bool {
        return true
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        return request
    }

    override func startLoading() {
        Self.lock.lock()
        Self.started += 1
        let handler = Self.handler
        Self.lock.unlock()
        handler?(self)
    }

    override func stopLoading() {
    }

    func respond(_ body: Data, status: Int = 200, headers: [String: String] = [:]) {
        self.client?.urlProtocol(self, didReceive: HTTPURLResponse(url: self.request.url!, statusCode: status, httpVersion: "HTTP/1.1", headerFields: headers)!, cacheStoragePolicy: .notAllowed)
        self.client?.urlProtocol(self, didLoad: body)
        self.client?.urlProtocolDidFinishLoading(self)
    }

    static func reset(_ handler: @escaping (StubURLProtocol) -> Void) {
        self.lock.lock()
        self.started = 0
        self.handler = handler
        self.lock.unlock()
    }

    static var startCount: Int {
        self.lock.lock()
        defer { self.lock.unlock() }
        return self.started
    }
}

private let configuration = JoboxTranslationConfiguration(endpoint: "https://translation.invalid/functions/v1/translate", tenant: "test-tenant", project: "test-project")

private func client(_ configuration: JoboxTranslationConfiguration = configuration, token: String = "synthetic-test-only") -> JoboxTranslationClient {
    let session = URLSessionConfiguration.ephemeral
    session.protocolClasses = [StubURLProtocol.self]
    return JoboxTranslationClient(configuration: configuration, token: token, sessionConfiguration: session)
}

private func json(_ value: [String: Any]) -> Data {
    return try! JSONSerialization.data(withJSONObject: value)
}

private func requestBody(_ request: URLRequest) throws -> Data {
    if let body = request.httpBody { return body }
    guard let stream = request.httpBodyStream else { throw TestFailure.failed("missing request body") }
    stream.open()
    defer { stream.close() }
    var body = Data()
    let capacity = 1024
    var buffer = [UInt8](repeating: 0, count: capacity)
    while true {
        let count = stream.read(&buffer, maxLength: capacity)
        guard count >= 0 else { throw stream.streamError ?? TestFailure.failed("request body stream failed") }
        if count == 0 { return body }
        body.append(contentsOf: buffer.prefix(count))
    }
}

private func translate(_ client: JoboxTranslationClient = client(), texts: [String] = ["hello"], language: String = "zh", task: JoboxTranslationTask = .incoming) throws -> Result<[String], JoboxTranslationError> {
    let semaphore = DispatchSemaphore(value: 0)
    let lock = NSLock()
    var result: Result<[String], JoboxTranslationError>?
    let handle = client.translate(texts: texts, targetLanguage: language, task: task) { value in
        lock.lock()
        result = value
        lock.unlock()
        semaphore.signal()
    }
    let waited = semaphore.wait(timeout: .now() + 5)
    withExtendedLifetime(handle) {}
    try check(waited == .success, "request completed")
    lock.lock()
    defer { lock.unlock() }
    return result!
}

private func isFailure(_ result: Result<[String], JoboxTranslationError>, _ expected: JoboxTranslationError) -> Bool {
    if case let .failure(error) = result {
        return error == expected
    }
    return false
}

private func testParagraphAndProtocol() throws {
    let fixtureBody = Data("one paragraph\n第二行".utf8)
    var fixtureRequest = URLRequest(url: URL(string: "https://translation.invalid/")!)
    fixtureRequest.httpBody = fixtureBody
    try check(try requestBody(fixtureRequest) == fixtureBody, "Data request body read intact")
    fixtureRequest.httpBody = nil
    fixtureRequest.httpBodyStream = InputStream(data: fixtureBody)
    try check(try requestBody(fixtureRequest) == fixtureBody, "stream request body read intact")
    let lock = NSLock()
    var captured: (request: URLRequest, body: Data)?
    StubURLProtocol.reset { stub in
        lock.lock()
        captured = (stub.request, try! requestBody(stub.request))
        lock.unlock()
        stub.respond(json(["ok": true, "translations": ["整段译文"]]))
    }
    let paragraph = "one paragraph\nwith a second line"
    let result = try translate(texts: [paragraph])
    try check(try result.get() == ["整段译文"], "incoming result")
    lock.lock()
    let request = captured!.request
    let capturedBody = captured!.body
    lock.unlock()
    let body = try JSONSerialization.jsonObject(with: capturedBody) as! [String: Any]
    try check(StubURLProtocol.startCount == 1, "paragraph is one request")
    try check(request.httpMethod == "POST", "POST")
    try check(request.timeoutInterval == 30, "fixed timeout")
    try check(request.value(forHTTPHeaderField: "x-jobox-cs-token") == "synthetic-test-only", "token header")
    try check(request.value(forHTTPHeaderField: "Authorization") == nil, "no unrelated authorization")
    try check(body["texts"] as? [String] == [paragraph], "whole paragraph")
    try check(body["tenant_slug"] as? String == "test-tenant" && body["project_slug"] as? String == "test-project", "ordinary scope fields")
    try check(body["tenant_id"] == nil && body["project_id"] == nil, "no unsupported scope aliases")
    try check(body["source_language"] as? String == "auto" && body["target_language"] as? String == "zh", "incoming language")
    try check(body["task"] == nil && body["reply_language"] == nil, "incoming is not outbound")
}

private func testOutbound() throws {
    let lock = NSLock()
    var body: [String: Any] = [:]
    StubURLProtocol.reset { stub in
        lock.lock()
        body = try! JSONSerialization.jsonObject(with: requestBody(stub.request)) as! [String: Any]
        lock.unlock()
        stub.respond(json(["ok": true, "translatedText": "draft only"]))
    }
    let result = try translate(texts: ["只改草稿"], language: "en", task: .outbound)
    try check(try result.get() == ["draft only"], "single translatedText accepted")
    lock.lock()
    let requestBody = body
    lock.unlock()
    try check(requestBody["task"] as? String == "outbound", "outbound task")
    try check(requestBody["reply_language_mode"] as? String == "fixed" && requestBody["reply_language"] as? String == "en", "outbound language mode")
}

private func testEnabledPreference() throws {
    try check(configuration.enabled, "configuration enabled defaults true")
    var disabled = configuration
    disabled.enabled = false
    try check(disabled.isValid, "enabled preference does not invalidate endpoint configuration")
    let encodedConfiguration = try JSONSerialization.jsonObject(with: JSONEncoder().encode(disabled)) as! [String: Any]
    try check(encodedConfiguration["enabled"] as? Bool == false, "enabled preference persists in ordinary configuration")
    let lock = NSLock()
    var body: [String: Any] = [:]
    StubURLProtocol.reset { stub in
        lock.lock()
        body = try! JSONSerialization.jsonObject(with: requestBody(stub.request)) as! [String: Any]
        lock.unlock()
        stub.respond(json(["ok": true, "translations": ["translation"]]))
    }
    let result = try translate(client(disabled))
    try check(try result.get() == ["translation"], "transport does not own account routing preference")
    lock.lock()
    let requestBody = body
    lock.unlock()
    try check(requestBody["enabled"] == nil, "enabled preference never sent in request body")
}

private func testValidationDoesNotStartRequest() throws {
    StubURLProtocol.reset { _ in }
    for endpoint in ["http://translation.invalid/functions/v1/translate", "https://translation.invalid/other", "https://user:password@translation.invalid/functions/v1/translate", "https://translation.invalid/functions/v1/translate?key=synthetic", "https://translation.invalid/functions/v1/translate#fragment"] {
        var invalid = configuration
        invalid.endpoint = endpoint
        try check(isFailure(try translate(client(invalid)), .invalidConfiguration), "invalid endpoint")
    }
    var invalid = configuration
    invalid.tenant = ""
    try check(isFailure(try translate(client(invalid)), .invalidConfiguration), "missing tenant")
    invalid = configuration
    invalid.project = "\nproject"
    try check(isFailure(try translate(client(invalid)), .invalidConfiguration), "invalid project")
    invalid.project = "project/path"
    try check(isFailure(try translate(client(invalid)), .invalidConfiguration), "project must be server slug")
    invalid.project = String(repeating: "x", count: 64)
    try check(isFailure(try translate(client(invalid)), .invalidConfiguration), "project slug length")
    invalid = configuration
    invalid.incomingLanguage = "zh\n"
    try check(isFailure(try translate(client(invalid)), .invalidConfiguration), "invalid incoming language")
    for token in ["", "token\nheader", "token with spaces"] {
        try check(isFailure(try translate(client(token: token)), .invalidConfiguration), "invalid token")
    }
    try check(isFailure(try translate(texts: []), .invalidRequest), "empty batch")
    try check(isFailure(try translate(texts: [String](repeating: "x", count: 21)), .invalidRequest), "batch limit")
    try check(isFailure(try translate(texts: [""]), .invalidRequest), "empty source")
    try check(isFailure(try translate(language: "en\r\n"), .invalidRequest), "target language")
    try check(StubURLProtocol.startCount == 0, "validation sends nothing")
}

private func testUTF16TextLimit() throws {
    for text in [String(repeating: "a", count: 4000), String(repeating: "😀", count: 2000)] {
        try check(text.utf16.count == 4000, "valid fixture has 4000 UTF16 code units")
        StubURLProtocol.reset { $0.respond(json(["ok": true, "translations": ["whole translation"]])) }
        let result = try translate(texts: [text])
        try check(try result.get() == ["whole translation"] && StubURLProtocol.startCount == 1, "4000 code units sent once")
    }
    for text in [String(repeating: "a", count: 4001), String(repeating: "😀", count: 2000) + "a", String(repeating: "😀", count: 2001)] {
        try check(text.utf16.count > 4000, "invalid fixture exceeds UTF16 limit")
        StubURLProtocol.reset { _ in }
        try check(isFailure(try translate(texts: [text]), .invalidRequest), "overlong paragraph rejected")
        try check(StubURLProtocol.startCount == 0, "overlong paragraph not truncated or sent")
    }
}

private func testResponseValidation() throws {
    let invalidBodies: [[String: Any]] = [
        ["ok": false, "translations": ["x"]],
        ["ok": 1, "translations": ["x"]],
        ["translations": ["x"]],
        ["ok": true, "translations": []],
        ["ok": true, "translations": ["x", "y"]],
        ["ok": true, "translations": [42]],
        ["ok": true, "translations": [""]]
    ]
    for body in invalidBodies {
        StubURLProtocol.reset { $0.respond(json(body)) }
        try check(isFailure(try translate(), .invalidResponse), "invalid response envelope")
    }
    StubURLProtocol.reset { $0.respond(Data("not JSON".utf8)) }
    try check(isFailure(try translate(), .invalidResponse), "invalid JSON")
    StubURLProtocol.reset { $0.respond(json(["ok": true, "translatedText": "one"] )) }
    try check(isFailure(try translate(texts: ["a", "b"]), .invalidResponse), "multi response cardinality")
    StubURLProtocol.reset { $0.respond(json(["ok": true, "translations": ["x"]]), status: 429) }
    try check(isFailure(try translate(), .httpStatus(429)), "HTTP error")
    StubURLProtocol.reset { $0.client?.urlProtocol($0, didFailWithError: URLError(.timedOut)) }
    try check(isFailure(try translate(), .network), "transport failure")
}

private func testResponseByteLimit() throws {
    StubURLProtocol.reset { $0.respond(Data(), headers: ["Content-Length": "1048577"]) }
    try check(isFailure(try translate(), .responseTooLarge), "declared response size")
    StubURLProtocol.reset { stub in
        let response = HTTPURLResponse(url: stub.request.url!, statusCode: 200, httpVersion: "HTTP/1.1", headerFields: [:])!
        stub.client?.urlProtocol(stub, didReceive: response, cacheStoragePolicy: .notAllowed)
        let chunk = Data(repeating: 32, count: 524_288)
        stub.client?.urlProtocol(stub, didLoad: chunk)
        stub.client?.urlProtocol(stub, didLoad: chunk)
        stub.client?.urlProtocol(stub, didLoad: Data([32]))
        stub.client?.urlProtocolDidFinishLoading(stub)
    }
    try check(isFailure(try translate(), .responseTooLarge), "incremental response size")
}

private func testRedirects() throws {
    #if !os(Linux)
    for destination in ["https://translation.invalid/functions/v1/other", "https://other.invalid/functions/v1/translate", "http://translation.invalid/functions/v1/translate"] {
        StubURLProtocol.reset { stub in
            let response = HTTPURLResponse(url: stub.request.url!, statusCode: 302, httpVersion: "HTTP/1.1", headerFields: ["Location": destination])!
            stub.client?.urlProtocol(stub, wasRedirectedTo: URLRequest(url: URL(string: destination)!), redirectResponse: response)
        }
        try check(isFailure(try translate(), .redirectRejected), "all redirects rejected")
        try check(StubURLProtocol.startCount == 1, "redirect destination not requested")
    }
    #endif
}

private func testCancellationCompletesOnce() throws {
    let started = DispatchSemaphore(value: 0)
    let completed = DispatchSemaphore(value: 0)
    let lock = NSLock()
    var completions = 0
    var result: Result<[String], JoboxTranslationError>?
    StubURLProtocol.reset { _ in started.signal() }
    let handle = client().translate(texts: ["hello"], targetLanguage: "zh") { value in
        lock.lock()
        completions += 1
        result = value
        lock.unlock()
        completed.signal()
    }
    try check(started.wait(timeout: .now() + 5) == .success, "cancel request started")
    handle.cancel()
    handle.cancel()
    try check(completed.wait(timeout: .now() + 5) == .success, "cancel completed")
    Thread.sleep(forTimeInterval: 0.05)
    lock.lock()
    let count = completions
    let cancelled = result!
    lock.unlock()
    try check(count == 1 && isFailure(cancelled, .cancelled), "cancel completion once")
}

private func testDraftResultSafety() throws {
    func snapshot(peer: Int64 = 11, thread: Int64? = 7, text: NSAttributedString = NSAttributedString(string: "draft"), language: String = "en", provider: String = "jobox") -> JoboxDraftIdentity {
        return JoboxDraftIdentity(peerId: peer, threadId: thread, text: text, language: language, provider: provider)
    }
    let original = snapshot()
    let gate = JoboxDraftRequestGate()
    let firstRequest = gate.begin(original)
    try check(gate.accepts(firstRequest, current: original), "unchanged draft accepts result")
    let formattedText = NSAttributedString(string: "draft", attributes: [NSAttributedString.Key("synthetic-style"): "bold"])
    let changed = [
        snapshot(peer: 12),
        snapshot(thread: 8),
        snapshot(thread: nil),
        snapshot(text: NSAttributedString(string: "edited draft")),
        snapshot(text: formattedText),
        snapshot(language: "fr"),
        snapshot(provider: "gtranslate")
    ]
    for identity in changed {
        try check(!gate.accepts(firstRequest, current: identity), "changed conversation, draft, language or provider rejects result")
    }
    let newerRequest = gate.begin(original)
    try check(!gate.accepts(firstRequest, current: original), "old result rejected even for identical repeated draft")
    try check(gate.accepts(newerRequest, current: original), "latest request accepts unchanged draft")
    gate.cancel()
    try check(!gate.accepts(newerRequest, current: original), "cancel rejects queued late result")

    let mutable = NSMutableAttributedString(string: "draft")
    let frozen = snapshot(text: mutable)
    mutable.append(NSAttributedString(string: " changed"))
    try check(frozen.text.string == "draft", "identity snapshots mutable draft")
}

@main
private enum JoboxTranslationTransportTests {
    static func main() {
        do {
            try testParagraphAndProtocol()
            try testOutbound()
            try testEnabledPreference()
            try testValidationDoesNotStartRequest()
            try testUTF16TextLimit()
            try testResponseValidation()
            try testResponseByteLimit()
            try testRedirects()
            try testCancellationCompletesOnce()
            try testDraftResultSafety()
            print("Jobox translation transport: 10 synthetic checks passed")
        } catch {
            fputs("Jobox translation transport check failed: \(error)\n", stderr)
            exit(1)
        }
    }
}
