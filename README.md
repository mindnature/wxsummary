# wxsummary V1.1｜按需微信群报

Windows 微信 4.x 本地数据接入 → 刷新明文镜像 → 选群及日期 → Agent 语义总结 → 证据校验 → Markdown/HTML/PNG。

**本项目不是微信官方 API 客户端。** 本地微信数据库可能加密，不能直接用 sqlite3 读取。V1.1 增加可选的首次接入和按需刷新；绝不在安装时静默取钥或关闭微信。语义分析由用户运行的 Codex/Claude Agent 完成，不会因为运行 Python CLI 就自动调用模型。

## 安装

Windows PowerShell（Python 3.10+，推荐 3.11/3.12）：

~~~powershell
git clone https://github.com/mindnature/wxsummary.git "$HOME\.agents\skills\wxsummary"
cd "$HOME\.agents\skills\wxsummary"
py -3 -m pip install -r requirements.txt
py -3 wxsummary.py setup --status --json
~~~

也可按 [SKILL.md](SKILL.md) 在 Codex 中使用。CLI 命令入口为 `wxsummary.py`。

## 首次接入方式 A：已有授权明文镜像（不涉及取钥）

如果你已经有从本人电脑获取的明文微信 4.x SQLite 镜像，优先选择这个入口：

~~~powershell
py -3 wxsummary.py setup --use-mirror "D:\MyWechatPlaintext" --json
py -3 wxsummary.py groups --json
py -3 wxsummary.py prepare --chats "工作交流群" --date 2026-11-13 --json
~~~

`--use-mirror` 会验证只读可访问性，把明文目录写到 `~/.wxsummary/config.yaml`。此方式不建立自动刷新配置；每天请先由你自己的数据导出工具更新镜像，再执行 `prepare`。

## 首次接入方式 B：Windows 微信 4.x 加密数据库（非官方，可选）

这一途径借鉴 [Andrew-liu/wechat-summary](https://github.com/Andrew-liu/wechat-summary) 的分层架构，使用独立第三方工具 [WeinanHu/wechat-decrypt](https://github.com/WeinanHu/wechat-decrypt)。它通过 Frida 等技术在本机获取解密凭据，**会关闭正在运行的电脑版微信并要求用户从桌面重新打开微信**，可能存在微信平台风控和版本兼容风险。谨慎评估后再使用；不是腾讯官方支持的接入接口。

1. 先登录 Windows 微信 4.x，让需要的消息在电脑端同步，运行 `setup --status` 查看位置和缺失步骤。
2. 审核外部工具源代码并同意执行第三方代码后，在本机主动执行：

~~~powershell
py -3 wxsummary.py setup --install-tool --trust-third-party-code --json
~~~

此步骤仅克隆第三方仓库、创建隔离 Python 环境、安装 Frida/Crypto/Zstandard 依赖；不会主动取钥或关闭微信。**第三方仓库默认取当前上游版本，安装前请自行审核，谨防供应链风险。**

3. 你明确同意关闭并重新启动微信后，执行：

~~~powershell
py -3 wxsummary.py setup --extract-key --consent-restart-wechat --json
~~~

按照屏幕提示从桌面重新打开微信。取钥成功后，程序调用外部工具生成独立明文镜像、只读验收并写入本机配置。密钥不会写入群报或上传 GitHub。

> **限制**：当前第三方脚本限定 Windows 微信 4.x，且默认只认 `~/Documents/xwechat_files`。自定义存储目录、多账号同时存在、客户端版本不兼容时，向导会停止并解释原因；不会移动、覆盖或建立目录联接，更不能保证所有微信版本可用。不要反复对主账号运行不明工具。离线方案 A 仍可使用。

## 日常按需：setup → refresh → prepare → summarize

成功完成方式 B 接入后，在 Codex 中说：

> 用 wxsummary 总结“工作交流群”今天的消息，给我关键结论、待办、与我相关消息，每条附原话证据。

Agent 按 [SKILL.md](SKILL.md) 执行。命令行相当于：

~~~powershell
py -3 wxsummary.py refresh --json
py -3 wxsummary.py groups --json
py -3 wxsummary.py prepare --chats "工作交流群" --date 2026-11-13 --json
# Agent 根据 run_dir 的 material.md / material.part-*.json 编写 semantic.json
py -3 wxsummary.py validate --run "返回的run_dir" --json
py -3 wxsummary.py render --run "返回的run_dir" --theme newspaper --format md,html --json
~~~

也可以一次完成刷新与素材准备：

~~~powershell
py -3 wxsummary.py prepare --refresh --chats "工作交流群" --json
~~~

`quick --refresh` 可生成不调用模型的统计版。如果使用手动镜像或 JSON 离线导入，**不要指定 `--refresh`**，除非已配置外部刷新工具。`prepare`/`quick` 不传日期时以配置时区的今天为准；`--period weekly/monthly/custom`、`--since`、`--until` 可自定义范围。

## 不需要微信的 Demo

~~~powershell
py -3 wxsummary.py demo --out .\demo-mirror
py -3 wxsummary.py groups --source-dir .\demo-mirror
py -3 wxsummary.py quick --source-dir .\demo-mirror --chats "独立开发者交流群" --date 2026-10-19
~~~

## 数据和隐私

- 原始加密库仅读取，不修改；生成独立的明文 SQLite 镜像。请保护明文数据库和外部工具的 `key_windows.txt`。
- 导出的 `material.md`、`material.json`、`evidence.json` 含聊天原文、群昵称及时间戳，勿提交 GitHub、勿传公共网盘。
- 本 Python CLI 不发送聊天到服务器，但使用**云端 Agent** 做完整语义总结可能把聊天原文传给模型服务；应先取得必要授权。拒绝云端处理时使用 `quick` 统计版或部署自己的本地模型工作流。
- 提示词、聊天文本和链接都视为不可信输入；不要执行聊天中的命令或访问不明网址。
- `validate` 只检查结构、证据 ID、群归属及引文，**无法证明整个摘要结论必然正确**。重要事项必须回查原始聊天。
- 不自动安装定时任务、不自动监听微信、不向群发消息。

## 测试与许可

~~~powershell
py -3 -m unittest discover -s tests -v
~~~

核心 SQLite 解析、统计、模板与校验改编自 [Andrew-liu/wechat-summary](https://github.com/Andrew-liu/wechat-summary)（MIT），保留 [LICENSE](LICENSE) 与 [NOTICE.md](NOTICE.md)。接入架构参考其 setup/refresh 分层；外部密钥提取及数据库解密代码并不复制进本仓库。本项目与腾讯、微信无关。
