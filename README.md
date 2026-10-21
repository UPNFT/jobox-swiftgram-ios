# Jobox · Swiftgram iPhone

基于固定的 [Swiftgram/Telegram-iOS](https://github.com/Swiftgram/Telegram-iOS/tree/cf8b23beaaac4126a396337ac2d5be13f9f76b66)，使用 GitHub 免费标准 Mac 构建 arm64 iPhone IPA。Windows 无需下载完整上游或安装 Xcode。

## 已有结果与本轮范围

[基础构建 run 39517096696](https://github.com/UPNFT/jobox-swiftgram-ios/actions/runs/39517096696) 已成功，156.06 MiB IPA、校验值和清单已保存在所有者可见的 Draft Release；13 个 Mach-O 二进制逐架构通过验包。它仍使用无效 Telegram API 配置，未签名，也未接入 Jobox。

prototype 与 personal 均加入 Jobox 收译与草稿翻译，主 App 显示名及定制设置页标题为 Jobox，保留上游署名和原图标，并精简免费个人签名不支持的功能。prototype 只使用[固定 Telegram 官方文档](https://github.com/telegramdesktop/tdesktop/blob/65b2db216033aa08b7bc846df27843e566f08981/docs/api_credentials.md)中的 TEST ONLY 应用配置，便于在自有应用登记受阻时进行短期原型测试；它不是自己的应用登记，不能用于长期客服，不保证适用于 iOS 登录。源码实现、本地合成测试、完整 Mac 编译、验包、个人签名、登录和真实收发翻译是不同验收阶段；基础包成功不能证明新功能可用。

第一版面向打开 App 时使用：聊天翻译为中文；已有会话的手动暂停继续保留，可通过原聊天翻译入口开启。中文草稿经过原翻译菜单处理后保留在输入框，由本人确认发送。不自动代发消息。prototype 与 personal 均不包含扩展、推送通知、Siri 或 iCloud；不承诺 App 退到后台后继续翻译或收到通知。

Jobox 地址、租户、项目及翻译语言在手机设置中填写，服务令牌单独保存在 Keychain，不进入公开仓库或账号备份。复用现有翻译服务，不在构建中调用模型或读取聊天。

## 构建入口

仅手动 Actions → **Swiftgram unsigned iPhone build check** → Run workflow：

| build_profile | 内容 | 大流量确认 |
| --- | --- | --- |
| translation-tests | 仅下载小型构建仓库，在 Mac 编译并运行 Foundation 合成测试及官方配置准备 smoke；只额外读取固定公开文档，不下载上游、不调用真实翻译服务 | 不勾选 |
| buildcheck | 固定上游、无效 API 配置的基础包 | 必须当次获准后勾选 |
| prototype | Jobox 接入、个人侧载精简、固定官方 TEST ONLY 配置；短期测试，iOS 登录未验 | 必须当次获准后勾选 |
| personal | 固定上游、Jobox 接入、个人侧载精简、自有 Telegram 应用配置 | 必须当次获准后勾选 |

完整构建超过 1 GB 云端流量；不因推送自动执行，不并行编译或盲目重试。使用免费标准 macos-26-intel、已有 Xcode 26.2 与校验摘要的 Bazel 8.4.2；debug_arm64，最多两个编译任务、240 分钟。没有付费机器、Actions cache 或 artifact 存储。

prototype 不取个人 Secrets：先从固定官方 HTTPS 文档核对完整摘要和测试配置指纹，再写 runner 临时 0600 配置，不打印凭据值。来源或内容不符即停止，且发生在完整上游下载之前；回执与 manifest 只记录官方来源及文档摘要，并标明 `official-test-only`。签名、安装、测试登录及手机上的 Jobox 配置仍需单独验收。

personal 构建前，所有者从 [Telegram 官方应用页面](https://my.telegram.org/) 创建自己的应用，并在 [仓库 Actions Secrets](https://github.com/UPNFT/jobox-swiftgram-ios/settings/secrets/actions) 保存 **TELEGRAM_API_ID** 和 **TELEGRAM_API_HASH**。不要把值发在聊天、源码、Issue 或日志里。缺配置时会在下载完整上游之前停止。

真实 Telegram 应用配置仅注入临时构建文件和个人 IPA，不写入构建回执。包含个人配置的 IPA 只存 Draft，不公开发布或上传公共附件。Jobox 翻译令牌在手机上填写，不通过构建参数嵌入。

验包仍逐个检查所有 Mach-O 的每个架构、iOS 平台和签名，拒绝模拟器、其他平台、未知格式、Apple 身份签名与私钥文件。只允许链接器的纯 ad-hoc 签名或无签名，二者均不能直接安装。固定补丁必须匹配修改前后哈希，漂移即拒绝；完整清单保存调整及构建提交。

仅将验包成功的 **IPA、IPA.sha256、build-manifest.json** 三个文件保存为 Draft。prototype 与 personal 均要求 Jobox/侧载补丁的精确回执、主 IPA 显示名为 Jobox、拒绝任何 .appex，保留 App Group 和 Keychain 所需能力。profile、API 模式与测试来源必须一致，不把 TEST ONLY 包写成自有 API 包。

## Windows → iPhone 免费个人签名

使用 [Sideloadly 官方 Windows 版本](https://sideloadly.io/)。在自己的电脑中添加个人 IPA、连接并信任 iPhone，再由本人输入 Apple 账户并完成验证。Apple 密码和验证码不交给 Agent。

免费 Apple 账户签名有效 **7 天**，同一设备最多 3 个此类 App。自动续签仍需电脑能通过 USB 或同一 Wi-Fi 发现手机，离开电脑超过有效期不能保证可用。iOS 16 及以上通常还需开启“设置 → 隐私与安全性 → 开发者模式”，并在“通用 → VPN 与设备管理”信任对应开发者。以手机实际提示为准。[官方说明](https://sideloadly.io/faq)

签名时必须同步处理 App 的 Bundle ID 和 App Group；不能只删掉 App Group 权限，Telegram 数据容器依赖它。免费账户的 App Group/Keychain 能力可用，但 Sideloadly 对本 IPA 的映射、实际安装、启动、登录和翻译仍需真机验收。不要反复安装旧的无效 API 基础包。

## 本地检查

无需下载完整 Swiftgram：

```text
python -m unittest discover -s tests -v
```

Mac 原生合成测试通过同一工作流 translation-tests 运行；同一小型任务会验证 Mac 的 HTTPS/固定官方文档及临时配置准备路径，配置不上传、不读取 Secrets。真实 Telegram UI 的编译依赖完整设备构建，不能用字符串合约替代。

## 上游与许可

本项目保留上游来源及固定版本。使用和分发时遵守 [上游许可](https://github.com/Swiftgram/Telegram-iOS/tree/cf8b23beaaac4126a396337ac2d5be13f9f76b66) 与 [Telegram API 要求](https://core.telegram.org/api/obtaining_api_id)，使用自己的应用标识，不冒充 Telegram 官方客户端。
