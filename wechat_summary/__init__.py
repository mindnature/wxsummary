"""wxsummary：把授权的微信群聊数据整理成按需群报（只读）。

确定性工作（读库、解析、统计、校验、渲染、同步）由本包完成；
语义编辑（主线、观点、待跟进）由调用本 Skill 的 Agent 完成，本包不调用任何 LLM。
"""
from __future__ import annotations

from pathlib import Path

__version__ = "1.2.0"

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
