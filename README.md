# wxsummary｜按需微信群报

一个本地优先的微信 4.x 群报 Agent Skill：**按需选群、指定日期、生成带原文证据的日报**。支持 Markdown、单文件 HTML、可选 PNG 及 8 个主题。由用户主动调用，**没有定时任务和后台监听**。

**安全边界：**本项目不取微信密钥、不 Hook/注入、不调用解密程序、不发送微信消息。只读取用户已经有权取得的**明文 SQLite 镜像**或标准化 JSON 导出。通过云端 Codex/Claude Code 做语义总结时，聊天素材可能发送至模型提供商；只有 `quick` 统计版不需要模型。

## Windows + Codex 安装

```powershell
git clone https://github.com/mindnature/wxsummary.git "$HOME\.agents\skills\wxsummary"
cd "$HOME\.agents\skills\wxsummary"
py -3 -m pip install -r requirements.txt
```

Python 3.10+，建议 3.11/3.12。之后在 Codex 里说：“使用 wxsummary 把工作交流群今天的聊天做成群报。”Agent 会按 [SKILL.md](SKILL.md) 执行；首次需你提供数据目录并允许模型处理相关内容。Claude Code 可将该仓库放到对应的 Skill 目录。

## 先跑 Demo：无需微信

```powershell
py -3 wxsummary.py demo --out .\demo-mirror
py -3 wxsummary.py groups --source-dir .\demo-mirror
py -3 wxsummary.py quick --source-dir .\demo-mirror --chats "独立开发者交流群" --date 2026-10-19
```

`quick` 立刻生成**纯统计版** Markdown/HTML，不调用模型。默认报告路径：`~/Documents/wechat-summary`。

## 每天按需使用

### A. 已有微信 4.x 明文 SQLite 镜像

该目录通常含 `db_storage/contact/contact.db` 和 `db_storage/message/message_*.db`；加密的 `xwechat_files` **不能**直接作为输入。

```powershell
py -3 wxsummary.py doctor --source-dir "D:\AuthorizedWechatExport"
py -3 wxsummary.py groups --source-dir "D:\AuthorizedWechatExport"
py -3 wxsummary.py quick --source-dir "D:\AuthorizedWechatExport" --chats "工作交流群" --date 2026-11-13
```

完整 AI 语义群报由 Agent 运行：

```powershell
py -3 wxsummary.py prepare --source-dir "D:\AuthorizedWechatExport" --chats "工作交流群" --date 2026-11-13
# Agent 读返回的 run_dir 中 material.md（及分块），写出 semantic.json
py -3 wxsummary.py validate --run "返回的run_dir"
py -3 wxsummary.py render --run "同一个run_dir" --theme newspaper --format md,html
```

不传日期时 `prepare` 与 `quick` 默认**今天**（按配置的时区）。反复提供 `--chats` 可选多群，`--mode separate` 可分群各出一份。`--since YYYY-MM-DDTHH:MM --until YYYY-MM-DDTHH:MM` 支持自定义时间窗。

### B. 使用聊天 JSON 导出

将以下结构存入 `messages.json`，此例均为虚构：

```json
{
  "chats": [{"id": "g1@chatroom", "name": "工作交流群"}],
  "messages": [
    {"msg_id":"m001","chat_id":"g1@chatroom","sender_id":"a","sender_name":"甲",
     "timestamp":"2026-11-13T09:00:00+08:00","type":"text","text":"建议下周一提交初稿。"}
  ]
}
```

```powershell
py -3 wxsummary.py groups --source-json .\messages.json
py -3 wxsummary.py quick --source-json .\messages.json --chats "工作交流群" --date 2026-11-13
```

## 自定义与隐私

复制 [config.example.yaml](config.example.yaml) 到 `~/.wechat-report/config.yaml`，可设置 `source.path`、本人 `me.names`/`me.ids`、常用群集合 `group_sets` 和输出位置 `output.root`。可选主题：newspaper、ink、claude、doodle、swiss、bento、brutal、terminal。PNG 长图需要本机 Chrome/Edge。

系统校验消息 ID、群归属和引文连续片段，**不等于能证明摘要结论真实**；提议不能冒充决议，群内观点不能当成外部事实，报告标注 `semantic_truth: not-verified`。@我识别和多媒体解析不是全覆盖，重要事项须回查原始消息。不要把明文数据库、素材和群报提交到 GitHub；含个人、单位或学生信息时，先确认处理权限。

## 测试、许可

```powershell
py -3 -m pip install -r requirements.txt
py -3 -m unittest discover -s tests -v
```

底层 SQLite 解析、统计、模板和校验基于 [Andrew-liu/wechat-summary](https://github.com/Andrew-liu/wechat-summary) 的 MIT 代码改造，保留原始 [LICENSE](LICENSE) 和 [NOTICE.md](NOTICE.md) 署名。本仓库与腾讯及微信无关联。
