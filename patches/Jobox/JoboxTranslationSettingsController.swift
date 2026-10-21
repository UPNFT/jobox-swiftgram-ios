import Foundation
import UIKit
import AsyncDisplayKit
import Display
import AccountContext
import SGSimpleSettings
import SGGTranslate
import TelegramPresentationData

public func joboxTranslationSettingsController(context: AccountContext) -> ViewController {
    return JoboxTranslationSettingsController(context: context)
}

private final class JoboxTranslationSettingsController: ViewController {
    private let accountId: Int64
    private let scrollView = UIScrollView()
    private let fields = UIStackView()
    private let endpoint = UITextField()
    private let tenant = UITextField()
    private let project = UITextField()
    private let incomingLanguage = UITextField()
    private let token = UITextField()
    private let enabled = UISwitch()
    private let status = UILabel()

    init(context: AccountContext) {
        self.accountId = context.account.peerId.id._internalGetInt64Value()
        super.init(navigationBarPresentationData: NavigationBarPresentationData(presentationData: context.sharedContext.currentPresentationData.with { $0 }))
        self.title = "Jobox 翻译"
        self.navigationItem.rightBarButtonItem = UIBarButtonItem(title: "保存", style: .done, target: self, action: #selector(self.save))
    }

    required init(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func loadDisplayNode() {
        self.displayNode = ASDisplayNode()
        self.displayNode.backgroundColor = .systemBackground
        self.displayNodeDidLoad()
        let root = self.displayNode.view
        self.scrollView.translatesAutoresizingMaskIntoConstraints = false
        self.fields.translatesAutoresizingMaskIntoConstraints = false
        self.fields.axis = .vertical
        self.fields.spacing = 12
        self.scrollView.keyboardDismissMode = .interactive
        root.addSubview(self.scrollView)
        self.scrollView.addSubview(self.fields)
        NSLayoutConstraint.activate([
            self.scrollView.leadingAnchor.constraint(equalTo: root.safeAreaLayoutGuide.leadingAnchor),
            self.scrollView.trailingAnchor.constraint(equalTo: root.safeAreaLayoutGuide.trailingAnchor),
            self.scrollView.topAnchor.constraint(equalTo: root.safeAreaLayoutGuide.topAnchor),
            self.scrollView.bottomAnchor.constraint(equalTo: root.safeAreaLayoutGuide.bottomAnchor),
            self.fields.leadingAnchor.constraint(equalTo: self.scrollView.contentLayoutGuide.leadingAnchor, constant: 20),
            self.fields.trailingAnchor.constraint(equalTo: self.scrollView.contentLayoutGuide.trailingAnchor, constant: -20),
            self.fields.topAnchor.constraint(equalTo: self.scrollView.contentLayoutGuide.topAnchor, constant: 20),
            self.fields.bottomAnchor.constraint(equalTo: self.scrollView.contentLayoutGuide.bottomAnchor, constant: -20),
            self.fields.widthAnchor.constraint(equalTo: self.scrollView.frameLayoutGuide.widthAnchor, constant: -40)
        ])
        self.addField(self.endpoint, label: "HTTPS 翻译接口", placeholder: "https://example.invalid/functions/v1/translate")
        self.endpoint.keyboardType = .URL
        self.addField(self.tenant, label: "Tenant", placeholder: "租户标识")
        self.addField(self.project, label: "Project", placeholder: "项目标识")
        self.addField(self.incomingLanguage, label: "收信译成", placeholder: "zh")
        self.addField(self.token, label: "访问令牌（仅保存在本机钥匙串）", placeholder: "已保存时留空即可")
        self.token.isSecureTextEntry = true
        self.token.textContentType = .password
        let explanation = UILabel()
        explanation.numberOfLines = 0
        explanation.font = .preferredFont(forTextStyle: .footnote)
        explanation.text = "仅为当前账号配置。保存后使用 Jobox 翻译；已暂停的会话会继续暂停，可在聊天内开启翻译。来信自动显示译文；中文草稿从发送菜单翻译后由你检查并按发送。翻译失败保留原文，不转发给其他翻译服务。"
        self.fields.addArrangedSubview(explanation)
        let enableRow = UIStackView()
        enableRow.axis = .horizontal
        let enableLabel = UILabel()
        enableLabel.text = "为当前账号启用 Jobox"
        enableLabel.font = .preferredFont(forTextStyle: .body)
        enableRow.addArrangedSubview(enableLabel)
        self.enabled.accessibilityLabel = enableLabel.text
        enableRow.addArrangedSubview(self.enabled)
        self.fields.addArrangedSubview(enableRow)
        self.status.numberOfLines = 0
        self.status.font = .preferredFont(forTextStyle: .footnote)
        self.status.accessibilityTraits = .staticText
        self.fields.addArrangedSubview(self.status)
        let clear = UIButton(type: .system)
        clear.setTitle("删除本账号 Jobox 配置和令牌", for: .normal)
        clear.addTarget(self, action: #selector(self.clear), for: .touchUpInside)
        self.fields.addArrangedSubview(clear)
        if let configuration = JoboxTranslationSettings.configuration(accountId: self.accountId) {
            self.endpoint.text = configuration.endpoint
            self.tenant.text = configuration.tenant
            self.project.text = configuration.project
            self.incomingLanguage.text = configuration.incomingLanguage
            self.enabled.isOn = configuration.enabled
        } else {
            self.endpoint.text = "https://vpizlreacuneuecyipts.supabase.co/functions/v1/translate"
            self.tenant.text = "demo"
            self.project.text = "demo"
            self.incomingLanguage.text = "zh"
            self.enabled.isOn = true
        }
    }

    override func containerLayoutUpdated(_ layout: ContainerViewLayout, transition: ContainedViewLayoutTransition) {
        super.containerLayoutUpdated(layout, transition: transition)
        self.scrollView.contentInset.bottom = layout.inputHeight ?? 0
        self.scrollView.verticalScrollIndicatorInsets.bottom = layout.inputHeight ?? 0
    }

    private func addField(_ field: UITextField, label: String, placeholder: String) {
        let heading = UILabel()
        heading.text = label
        heading.font = .preferredFont(forTextStyle: .subheadline)
        heading.adjustsFontForContentSizeCategory = true
        self.fields.addArrangedSubview(heading)
        field.placeholder = placeholder
        field.accessibilityLabel = label
        field.borderStyle = .roundedRect
        field.font = .preferredFont(forTextStyle: .body)
        field.adjustsFontForContentSizeCategory = true
        field.autocapitalizationType = .none
        field.autocorrectionType = .no
        field.spellCheckingType = .no
        field.heightAnchor.constraint(greaterThanOrEqualToConstant: 44).isActive = true
        self.fields.addArrangedSubview(field)
    }

    @objc private func save() {
        let configuration = JoboxTranslationConfiguration(
            endpoint: (self.endpoint.text ?? "").trimmingCharacters(in: .whitespacesAndNewlines),
            tenant: (self.tenant.text ?? "").trimmingCharacters(in: .whitespacesAndNewlines),
            project: (self.project.text ?? "").trimmingCharacters(in: .whitespacesAndNewlines),
            incomingLanguage: (self.incomingLanguage.text ?? "zh").trimmingCharacters(in: .whitespacesAndNewlines),
            enabled: self.enabled.isOn
        )
        guard configuration.isValid else {
            self.status.text = "请填写有效的 HTTPS 接口、Tenant、Project 和目标语言。"
            return
        }
        do {
            try JoboxTranslationSettings.saveConfiguration(configuration, accountId: self.accountId)
            if let token = self.token.text, !token.isEmpty {
                try JoboxTranslationSettings.setToken(token, accountId: self.accountId)
                self.token.text = nil
            }
            guard !configuration.enabled || JoboxTranslationSettings.isConfigured(accountId: self.accountId) else {
                self.status.text = "接口范围有变化或尚无令牌，请输入此接口对应的访问令牌再保存。"
                return
            }
            self.status.text = self.enabled.isOn ? "已为当前账号启用 Jobox。已暂停的会话请在聊天内开启翻译。" : "已停用当前账号 Jobox，恢复原翻译服务选择。"
        } catch {
            self.status.text = "配置未能保存，请检查本机钥匙串权限后重试。"
        }
    }

    @objc private func clear() {
        do {
            try JoboxTranslationSettings.clear(accountId: self.accountId)
            self.token.text = nil
            self.status.text = "已删除本账号配置，恢复原翻译服务选择。"
        } catch {
            self.status.text = "删除未完成，请重试。"
        }
    }
}
