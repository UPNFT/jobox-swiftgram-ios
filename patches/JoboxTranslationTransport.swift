import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

public struct JoboxTranslationConfiguration: Codable, Equatable {
    public var endpoint: String
    public var tenant: String
    public var project: String
    public var incomingLanguage: String
    public var enabled: Bool

    public init(endpoint: String, tenant: String, project: String, incomingLanguage: String = "zh", enabled: Bool = true) {
        self.endpoint = endpoint
        self.tenant = tenant
        self.project = project
        self.incomingLanguage = incomingLanguage
        self.enabled = enabled
    }

    public var isValid: Bool {
        return self.endpointURL != nil
            && Self.validIdentifier(self.tenant)
            && Self.validIdentifier(self.project)
            && Self.validLanguage(self.incomingLanguage)
    }

    private var endpointURL: URL? {
        guard let components = URLComponents(string: self.endpoint),
              components.scheme?.lowercased() == "https",
              let host = components.host, !host.isEmpty,
              components.user == nil, components.password == nil,
              components.query == nil, components.fragment == nil,
              components.path == "/functions/v1/translate" else {
            return nil
        }
        return components.url
    }

    private static func validIdentifier(_ value: String) -> Bool {
        return value.range(of: "\\A[A-Za-z0-9][A-Za-z0-9_-]{0,62}\\z", options: .regularExpression) != nil
    }

    fileprivate static func validLanguage(_ value: String) -> Bool {
        return !value.isEmpty && value.utf8.count <= 32
            && value.unicodeScalars.allSatisfy {
                (65...90).contains($0.value) || (97...122).contains($0.value)
                    || (48...57).contains($0.value) || $0.value == 45
            }
    }

    fileprivate var requestURL: URL? {
        return self.isValid ? self.endpointURL : nil
    }
}

public enum JoboxTranslationTask: String {
    case incoming
    case outbound
}

public enum JoboxTranslationError: Error, Equatable {
    case invalidConfiguration
    case invalidRequest
    case network
    case httpStatus(Int)
    case invalidResponse
    case responseTooLarge
    case redirectRejected
    case cancelled
}

public final class JoboxTranslationRequest {
    private let operation: JoboxTranslationOperation?

    fileprivate init(operation: JoboxTranslationOperation? = nil) {
        self.operation = operation
    }

    public func cancel() {
        self.operation?.cancel()
    }
}

public final class JoboxTranslationClient {
    public static let timeout: TimeInterval = 30
    public static let maximumResponseBytes = 1_048_576
    private let configuration: JoboxTranslationConfiguration
    private let token: String
    private let sessionConfiguration: URLSessionConfiguration

    public init(configuration: JoboxTranslationConfiguration, token: String, sessionConfiguration: URLSessionConfiguration = .ephemeral) {
        self.configuration = configuration
        self.token = token
        self.sessionConfiguration = sessionConfiguration
    }

    @discardableResult
    public func translate(texts: [String], targetLanguage: String, task: JoboxTranslationTask = .incoming, completion: @escaping (Result<[String], JoboxTranslationError>) -> Void) -> JoboxTranslationRequest {
        guard let url = self.configuration.requestURL,
              Self.validToken(self.token) else {
            completion(.failure(.invalidConfiguration))
            return JoboxTranslationRequest()
        }
        guard !texts.isEmpty, texts.count <= 20,
              texts.allSatisfy({ !$0.isEmpty && $0.utf16.count <= 4000 }),
              JoboxTranslationConfiguration.validLanguage(targetLanguage) else {
            completion(.failure(.invalidRequest))
            return JoboxTranslationRequest()
        }
        var body: [String: Any] = [
            "texts": texts,
            "source_language": "auto",
            "target_language": targetLanguage,
            "tenant_slug": self.configuration.tenant,
            "project_slug": self.configuration.project
        ]
        if task == .outbound {
            body["task"] = "outbound"
            body["reply_language_mode"] = "fixed"
            body["reply_language"] = targetLanguage
        }
        guard let encodedBody = try? JSONSerialization.data(withJSONObject: body) else {
            completion(.failure(.invalidRequest))
            return JoboxTranslationRequest()
        }
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: Self.timeout)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue(self.token, forHTTPHeaderField: "x-jobox-cs-token")
        request.httpBody = encodedBody

        let sessionConfiguration = self.sessionConfiguration.copy() as! URLSessionConfiguration
        sessionConfiguration.timeoutIntervalForRequest = Self.timeout
        sessionConfiguration.timeoutIntervalForResource = Self.timeout
        sessionConfiguration.requestCachePolicy = .reloadIgnoringLocalCacheData
        sessionConfiguration.urlCache = nil
        sessionConfiguration.httpCookieStorage = nil
        sessionConfiguration.httpShouldSetCookies = false
        sessionConfiguration.urlCredentialStorage = nil
        let operation = JoboxTranslationOperation(request: request, configuration: sessionConfiguration, expectedCount: texts.count, completion: completion)
        let handle = JoboxTranslationRequest(operation: operation)
        operation.start()
        return handle
    }

    static func validToken(_ value: String) -> Bool {
        return !value.isEmpty && value.utf8.count <= 4096
            && value.unicodeScalars.allSatisfy { (33...126).contains($0.value) }
    }
}

private final class JoboxTranslationOperation: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    private let request: URLRequest
    private let configuration: URLSessionConfiguration
    private let expectedCount: Int
    private let lock = NSLock()
    private var completion: ((Result<[String], JoboxTranslationError>) -> Void)?
    private var responseData = Data()
    private var session: URLSession?
    private var task: URLSessionDataTask?

    init(request: URLRequest, configuration: URLSessionConfiguration, expectedCount: Int, completion: @escaping (Result<[String], JoboxTranslationError>) -> Void) {
        self.request = request
        self.configuration = configuration
        self.expectedCount = expectedCount
        self.completion = completion
    }

    func start() {
        let queue = OperationQueue()
        queue.maxConcurrentOperationCount = 1
        let session = URLSession(configuration: self.configuration, delegate: self, delegateQueue: queue)
        self.session = session
        let task = session.dataTask(with: self.request)
        self.task = task
        task.resume()
    }

    func cancel() {
        self.finish(.failure(.cancelled))
    }

    private func finish(_ result: Result<[String], JoboxTranslationError>) {
        self.lock.lock()
        let completion = self.completion
        self.completion = nil
        self.responseData.removeAll(keepingCapacity: false)
        self.lock.unlock()
        guard let completion = completion else {
            return
        }
        self.task?.cancel()
        self.session?.invalidateAndCancel()
        self.task = nil
        self.session = nil
        completion(result)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse, completionHandler: @escaping @Sendable (URLSession.ResponseDisposition) -> Void) {
        guard let response = response as? HTTPURLResponse else {
            completionHandler(.cancel)
            self.finish(.failure(.invalidResponse))
            return
        }
        guard (200..<300).contains(response.statusCode) else {
            completionHandler(.cancel)
            self.finish(.failure(.httpStatus(response.statusCode)))
            return
        }
        guard response.expectedContentLength <= Int64(JoboxTranslationClient.maximumResponseBytes) else {
            completionHandler(.cancel)
            self.finish(.failure(.responseTooLarge))
            return
        }
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        self.lock.lock()
        guard self.completion != nil else {
            self.lock.unlock()
            return
        }
        guard data.count <= JoboxTranslationClient.maximumResponseBytes - self.responseData.count else {
            self.lock.unlock()
            self.finish(.failure(.responseTooLarge))
            return
        }
        self.responseData.append(data)
        self.lock.unlock()
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping @Sendable (URLRequest?) -> Void) {
        // The token belongs to the configured endpoint; no redirect may forward it.
        completionHandler(nil)
        self.finish(.failure(.redirectRejected))
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        if let error = error {
            let error = error as NSError
            self.finish(.failure(error.domain == NSURLErrorDomain && error.code == NSURLErrorCancelled ? .cancelled : .network))
            return
        }
        self.lock.lock()
        let data = self.responseData
        let isPending = self.completion != nil
        self.lock.unlock()
        guard isPending else {
            return
        }
        struct Response: Decodable {
            let ok: Bool
            let translations: [String]?
            let translatedText: String?
        }
        guard let response = try? JSONDecoder().decode(Response.self, from: data), response.ok else {
            self.finish(.failure(.invalidResponse))
            return
        }
        let translations: [String]?
        if let values = response.translations {
            translations = values
        } else if self.expectedCount == 1, let value = response.translatedText {
            translations = [value]
        } else {
            translations = nil
        }
        guard let values = translations, values.count == self.expectedCount,
              values.allSatisfy({ !$0.isEmpty }) else {
            self.finish(.failure(.invalidResponse))
            return
        }
        self.finish(.success(values))
    }
}
