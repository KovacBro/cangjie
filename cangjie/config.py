"""配置与路径解析。"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field, fields
from pathlib import Path

STATE_DIR_NAME = ".cangjie"

DEFAULT_EXTS = [
    ".md", ".txt", ".py", ".js", ".ts", ".tsx", ".jsx", ".vue", ".json",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".html", ".css", ".scss",
    ".sql", ".sh", ".ps1", ".java", ".go", ".rs", ".c", ".h", ".cpp",
    ".cs", ".rb", ".php", ".kt", ".swift", ".lua", ".proto", ".gradle",
]

SKIP_DIRS = {
    ".git", ".svn", ".hg", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", "target", ".idea", ".vscode", ".next", ".nuxt",
    ".cangjie", ".workbuddy", "site-packages", ".mypy_cache", ".pytest_cache",
}


@dataclass
class Config:
    # 载体池预设：auto(符号→汉字逐级) / sym(符号优先) / han(汉字优先) / forge(自造字)
    carrier_pool: str = "auto"
    # 配载体时要求的最小净赚（短语单价 - 载体单价 >= min_gain 才配）
    min_gain: float = 1.5
    # token 计量所用的 tiktoken 编码；缺失时自动退化为启发式估算
    encoding: str = "o200k_base"
    # 字典规模硬上限——控制后期不膨胀的关键阀门
    max_entries: int = 128
    # 一条候选短语至少出现多少次才值得造字
    min_freq: int = 3
    # 短语本身至少值多少 token 才考虑（<3 token 压缩收益太薄）
    min_phrase_cost: float = 3.0
    # 中文短语最少字数 / 英文短语最少词数
    min_len_cjk: int = 2
    min_len_latin: int = 2
    # 最长 n-gram 窗口与短语长度上限
    max_ngram_cjk: int = 10
    max_ngram_latin: int = 4
    max_phrase_chars: int = 28
    # 新词发现门槛：自由度（左右邻接熵，bit）与凝聚度
    min_freedom: float = 0.9
    min_cohesion: float = 2.0
    # 文档频次门槛（≥3 篇文档时，短语至少出现在 2 篇里）
    docs_gate: bool = True
    # 允许含数字的短语（默认关：数字跨文档不稳定，压缩价值低）
    allow_digits: bool = False
    # 只出现在受保护区（代码块/URL/路径）的短语不入库——因为根本不会被压缩
    skip_code_only: bool = True
    # 摊销系数：字典条目本身也要花 token，必须挣回 amortization 倍才收录
    amortization: float = 1.0
    # 代码块 / 行内代码 / URL / 路径 默认不参与压缩（防止破坏可执行内容）
    protect_code: bool = True
    # 原文中恰好出现载体字时，用反斜杠转义保证可逆
    strict_escape: bool = True
    # 扫描白名单与体积上限
    scan_exts: list = field(default_factory=lambda: list(DEFAULT_EXTS))
    max_file_bytes: int = 512 * 1024
    # 单次压缩的分块大小（大文本分块可降低内存峰值）
    chunk_chars: int = 20000

    @classmethod
    def load(cls, path: Path) -> "Config":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
        )


def find_root(start: Path | None = None, marker: str = STATE_DIR_NAME) -> Path:
    """向上寻找已初始化的工程根；找不到则返回当前目录。"""
    cur = (start or Path.cwd()).resolve()
    for cand in [cur, *cur.parents]:
        if (cand / marker).is_dir():
            return cand
    return cur


def state_dir(root: Path | None = None) -> Path:
    root = root or find_root()
    d = root / STATE_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path(root: Path | None = None) -> Path:
    return state_dir(root) / "config.json"


def dict_path(root: Path | None = None) -> Path:
    return state_dir(root) / "dict.json"


def ledger_path(root: Path | None = None) -> Path:
    return state_dir(root) / "ledger.jsonl"


def corpus_path(root: Path | None = None) -> Path:
    return state_dir(root) / "corpus.txt"


def load_config(root: Path | None = None) -> Config:
    return Config.load(config_path(root))


def save_config(cfg: Config, root: Path | None = None) -> None:
    cfg.save(config_path(root))
