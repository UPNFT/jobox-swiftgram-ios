import Foundation

// Pure Foundation so stale-result safety is exercised without a Telegram
// account, UIKit, a backend, or compiling the full Telegram dependency graph.
public struct JoboxDraftIdentity: Equatable {
    public let peerId: Int64
    public let threadId: Int64?
    public let text: NSAttributedString
    public let language: String
    public let provider: String

    public init(peerId: Int64, threadId: Int64?, text: NSAttributedString, language: String, provider: String) {
        self.peerId = peerId
        self.threadId = threadId
        self.text = NSAttributedString(attributedString: text)
        self.language = language
        self.provider = provider
    }

    public static func == (lhs: JoboxDraftIdentity, rhs: JoboxDraftIdentity) -> Bool {
        return lhs.peerId == rhs.peerId && lhs.threadId == rhs.threadId
            && lhs.text.isEqual(to: rhs.text) && lhs.language == rhs.language
            && lhs.provider == rhs.provider
    }
}

public final class JoboxDraftRequestGate {
    private var pending: (UUID, JoboxDraftIdentity)?
    public init() {}

    public func begin(_ identity: JoboxDraftIdentity) -> UUID {
        let request = UUID()
        self.pending = (request, identity)
        return request
    }

    public func cancel() {
        self.pending = nil
    }

    public func accepts(_ request: UUID, current: JoboxDraftIdentity) -> Bool {
        guard let (pendingRequest, original) = self.pending else { return false }
        return pendingRequest == request && original == current
    }
}
