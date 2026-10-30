"""仓颉 (Cangjie) —— 面向长任务的语义压缩与上下文治理引擎。

核心思想：把高频长词/长句折叠成一个"字"，用字典换取上下文长度的次线性增长；
再用「状态卡 + 滚动结论 + 定长包」把每轮成本钉成常数。

本包不依赖任何 Agent 框架，可独立用 CLI、MCP、或作为库调用。
"""

__version__ = "1.0.1"
__all__ = [
    "config", "tokenizer", "carriers", "mine", "codec", "glyphs", "glyphforge",
    "state", "memory", "trim", "sift", "budget", "report", "cli", "mcp_server",
]
