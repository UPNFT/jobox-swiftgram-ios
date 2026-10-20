# Jobox · Swiftgram iPhone 构建验证

先验证 [Swiftgram/Telegram-iOS](https://github.com/Swiftgram/Telegram-iOS) 能否在 GitHub 免费标准 macOS runner 上构建。固定源版本 `cf8b23beaaac4126a396337ac2d5be13f9f76b66`，不自动公开发布 Release。

## 当前范围

- 只接受手动触发，并要求确认本次云端超过 1 GB 的源码和依赖流量。
- 使用免费标准 `macos-26-intel` runner（14 GB RAM）、已有 Xcode 26.2、独立核验官方 Intel 摘要的 Bazel 8.4.2；不申请付费大机器，不装 Xcode。宿主机是 Intel，输出仍为 arm64 iPhone 设备包。
- 两个编译任务上限，单次最多 240 分钟；同一时间一个构建，无自动重试或计划任务。
- 仅将通过设备平台/签名检查的 IPA、SHA256 和简短清单存为供所有者检查的 Draft Release，IPA 上限 450 MiB；不使用可能超出免费额度的 Actions artifact/cache 存储，不上传 DSYM/源码目录。

**当前配置只用无效占位 API 信息，Swiftgram 附加服务指向 `.invalid` 域名。构建包未签名，不能直接安装或登录 Telegram。** 此阶段不包含真实聊天、翻译调用、客户发消息或 Apple 签名。

“未签名”指没有 Apple 开发者身份签名；允许链接器自动生成的纯 ad-hoc 签名，清单会区分 `none`/`adhoc`。两者都不能替代真机安装所需的个人签名。不会读取或导入上游假签名证书、私钥。

验包使用 Apple 原生工具逐个检查 Mach-O 架构：现代格式须明确为 iOS，旧格式须为 ARM 的 iPhoneOS；模拟器、其他平台、未知或混合格式均拒绝。失败日志保留包内文件路径、架构和平台命令摘要。

固定 rules_apple 的主 App、普通小组件两处 profile 打包条件会复用其公开签名判断，使无签名构建不再强制嵌入签名文件。正常签名的原有检查保留；修改前后源码哈希严格锁定，构建清单记录该调整及本仓库提交，源码漂移即停止。

## 运行

进入 Actions → Swiftgram unsigned iPhone build check → Run workflow。只有在人已经明确批准这一次大流量后，才能勾选 `large_transfer_approved`。创建仓库或推送源码均不会启动构建。

本地检查（无需获取 Swiftgram 源码）：

```text
python -m unittest discover -s tests -v
```

得到通过验包的 IPA 后，再处理个人 Telegram API 配置、iPhone 签名和真实装机。不要把 Apple/Telegram/模型密钥写入此公开仓库，也不要用公共 artifact 发布包含真实凭据的构建。

## 上游与许可

Swiftgram 基于 Telegram iOS。其构建过程、消息客户端和翻译功能属于上游，本仓库只是独立构建配置与验证脚本；客户端源码并未复制到本仓库。使用和分发客户端时须遵守上游许可及 Telegram API 条款，使用自有应用标识，不使用 Telegram 官方品牌冒充官方客户端。

[run 39412449784](https://github.com/UPNFT/jobox-swiftgram-ios/actions/runs/39412449784) 已在免费 runner 完成设备编译（1 小时 43 分 45 秒），后续验包失败，未保存 IPA。编译通过与验包、签名、iPhone 功能验收分开记录。
