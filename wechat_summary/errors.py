"""统一错误码与退出码。"""
from __future__ import annotations

from typing import Any

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_PARTIAL = 2

HINTS: dict[str, str] = {
    "config_invalid": "检查配置文件语法；可参考仓库根目录的 config.example.yaml。",
    "source_not_configured": "运行 setup 自动接入；或复制 config.example.yaml 到 ~/.wechat-report/config.yaml 并填写 source.path，或临时使用 --source-dir / --source-json。",
    "source_not_found": "运行 setup 自动安装外部工具并生成解密镜像（见 docs/setup-windows.md）。",
    "layout_ambiguous": "目录下发现多个账号，请在配置 source.account 中填写账号目录名的一部分（如 wxid_ 开头的片段）。",
    "database_unreadable": "确认文件是已解密的明文 SQLite（文件头应为 'SQLite format 3'），而不是微信原始加密库。",
    "schema_unrecognized": "表结构与微信 4.x 不一致，可在配置 source.paths 中显式指定 contact/session/message 库路径。",
    "zstd_unavailable": "当前 Python 不含 compression.zstd（3.14+ 自带），请执行 pip install zstandard。",
    "chat_not_found": "用 list-chats --filter 关键词 查询准确群名或群 ID。",
    "chat_ambiguous": "存在多个同名群，请改用群 ID（xxx@chatroom）指定。",
    "group_set_unknown": "在配置 group_sets 中定义该集合，或直接用 --chats 指定群。",
    "window_invalid": "检查 --date / --since / --until 格式，例如 2026-10-26 或 2026-10-26T08:00。",
    "run_not_found": "确认 --run 指向 prepare 生成的输出目录。",
    "material_invalid": "素材包损坏或版本不符，请重新运行 prepare。",
    "semantic_missing": "先阅读 material.md 并在同目录写 semantic.json；或使用 --stats-only 生成仅统计版。",
    "semantic_invalid": "按 errors 列表逐条修正 semantic.json 后重新运行 validate。",
    "report_missing": "先运行 validate（或 render --stats-only）生成 report.json。",
    "theme_not_found": "运行 list-themes 查看可用主题。",
    "render_failed": "检查主题模板语法，或换一个主题重试。",
    "refresh_not_configured": "运行 setup 自动写入 refresh 命令。",
    "refresh_failed": "密钥可能已失效（微信升级或换账号），用户同意后运行 setup --extract-key 重新取钥。",
    "setup_unsupported": "当前系统不支持自动接入。",
    "setup_conflict": "按提示处理冲突目录后重新运行 setup。",
    "setup_failed": "查看 details 中的输出；网络问题可重试，依赖安装失败可换用 Python 3.10~3.13。",
}


class WSError(Exception):
    """带错误码和修复提示的业务异常。"""

    def __init__(self, code: str, message: str, hint: str | None = None, details: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint if hint is not None else HINTS.get(code)
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.hint:
            error["hint"] = self.hint
        if self.details is not None:
            error["details"] = self.details
        return {"ok": False, "error": error}

    def __str__(self) -> str:
        text = f"[{self.code}] {self.message}"
        if self.hint:
            text += f"\n提示：{self.hint}"
        return text
