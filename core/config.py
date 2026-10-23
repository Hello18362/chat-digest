"""统一配置加载 / 环境变量 / 路径解析。

设计约定：
    · 所有可变参数（API 地址、模型名、群名、领导名单）只从 YAML / .env 读取
    · 代码里不出现字面量形式的业务参数
    · 配置项缺失时回落到 _DEFAULTS，不会因为改坏配置就崩掉整个程序
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from core.log import get_logger

logger = get_logger("config")

# --------------------------------------------------------------------------- #
# 路径常量
# --------------------------------------------------------------------------- #
PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]


def _resolve_config_dir() -> Path:
    """定位 config/ 目录。

    源码运行 / 可编辑安装（`pip install -e .`）时用项目根目录下的 config/；
    通过 wheel 安装时 config/ 作为包一起装进 site-packages，此时回落到包内路径。
    """
    source = PROJECT_ROOT / "config"
    if (source / "settings.yaml").is_file():
        return source

    try:
        import config as _config_pkg  # 顶层包名，与 core.config 不冲突

        packaged = Path(_config_pkg.__file__).resolve().parent
        if (packaged / "settings.yaml").is_file():
            return packaged
    except Exception:  # noqa: BLE001 - 拿不到包内配置就退回源码路径
        logger.debug("包内 config/ 不可用，回落到 %s", source)
    return source


CONFIG_DIR: Path = _resolve_config_dir()
PROMPT_DIR: Path = CONFIG_DIR / "prompts"

# 产物 / 日志 / .env 的落点：源码运行时是项目根目录；
# 安装后运行时是调用者的当前目录（不往 site-packages 里写东西）。
RUNTIME_ROOT: Path = PROJECT_ROOT if (PROJECT_ROOT / "config").is_dir() else Path.cwd()

# --------------------------------------------------------------------------- #
# 默认配置：这里的每一项都可以被 config/settings.yaml 覆盖
# --------------------------------------------------------------------------- #
_DEFAULTS: Dict[str, Any] = {
    "chatlog": {
        "api_base": "http://127.0.0.1:5030",
        "timeout": 15,
        "page_size": 500,
        "max_pages": 20,
        "endpoints": {
            "chatlog": "/api/v1/chatlog",
            "chatroom": "/api/v1/chatroom",
            "contact": "/api/v1/contact",
            "session": "/api/v1/session",
            "health": "/health",
            "voice": "/voice/{key}",
            "image": "/image/{key}",
            "video": "/video/{key}",
            "file": "/file/{key}",
            "data": "/data/{path}",
        },
    },
    "asr": {
        "enabled": True,
        "model": "large-v3",
        "device": "cpu",
        "compute_type": "int8",
        "language": "zh",
        "beam_size": 5,
        "vad_filter": True,
        "silk_decoder": "",
        "max_duration": 300,
    },
    "vision": {
        "provider": "dummy",
        "model": "doubao-vision-pro",
        "timeout": 30,
        "max_images": 20,
        "fallback_text": "图片",
    },
    "article": {
        "enabled": True,
        "timeout": 15,
        "max_chars": 3000,
        "retry": 1,
    },
    "summarizer": {
        "provider": "file_output",
        "prompt_template": "leader_brief",
        "model": "doubao-seed-1904",
        "pending_dir": "./runs/pending",
        "max_input_chars": 60000,
    },
    "output": {
        "dir": "./runs",
        "format": ["markdown"],
        "html_theme": "light",
        "include_meta": True,
    },
    "logging": {
        "level": "INFO",
        "dir": "./logs",
        "file": "hub.log",
        "max_bytes": 5242880,
        "backup_count": 5,
    },
    "web": {
        "enabled": True,
        "host": "127.0.0.1",
        "port": 5710,
        "open_browser": True,
    },
}


class ConfigError(RuntimeError):
    """配置文件缺失或格式非法时抛出。"""


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """把 override 递归合并进 base，返回新字典（不修改入参）。"""
    result: Dict[str, Any] = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def dig(data: Optional[Dict[str, Any]], dotted: str, default: Any = None) -> Any:
    """按点号路径安全取值：dig(cfg, "chatlog.api_base")。"""
    if not isinstance(data, dict):
        return default
    node: Any = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node if node is not None else default


def resolve_path(path: Any, mkdir: bool = False) -> Path:
    """把配置里的相对路径解析为规范化的绝对路径（相对的是项目根目录）。

    务必做 `resolve()`：`./runs` 与 `<root>/runs` 是同一个目录但字符串不同，
    不归一化的话，`target.parent == runs_dir` 这类比较会时灵时不灵。
    """
    p = Path(str(path)).expanduser()
    if not p.is_absolute():
        p = RUNTIME_ROOT / p
    try:
        p = p.resolve()
    except OSError:  # 路径含非法字符等极端情况，退化为非规范化形式
        logger.debug("路径规范化失败：%s", p)
    if mkdir:
        p.mkdir(parents=True, exist_ok=True)
    return p


# --------------------------------------------------------------------------- #
# .env 加载（不引入 python-dotenv，保持依赖最小化）
# --------------------------------------------------------------------------- #
def load_dotenv(env_path: Optional[Path] = None) -> Dict[str, str]:
    """读取 .env 并写入 os.environ。

    已存在的环境变量优先（不会被 .env 覆盖），返回本次实际写入的键值对。
    """
    path = env_path or (RUNTIME_ROOT / ".env")
    loaded: Dict[str, str] = {}
    if not path.exists():
        return loaded

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return loaded

    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ and value:
            os.environ[key] = value
            loaded[key] = value
    return loaded


# --------------------------------------------------------------------------- #
# 主配置
# --------------------------------------------------------------------------- #
def load_settings(path: Optional[Path] = None, reload_env: bool = True) -> Dict[str, Any]:
    """加载 settings.yaml，与默认值深合并后返回。"""
    if reload_env:
        load_dotenv()

    target = Path(path) if path else (CONFIG_DIR / "settings.yaml")
    user_cfg: Dict[str, Any] = {}
    if target.exists():
        try:
            user_cfg = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:  # 配置写坏了也要能跑，用默认值兜底
            raise ConfigError(f"settings.yaml 解析失败：{exc}") from exc
    else:
        print(f"[config] 未找到 {target.name}，改用内置默认配置")

    if not isinstance(user_cfg, dict):
        raise ConfigError("settings.yaml 顶层必须是键值映射")

    cfg = deep_merge(_DEFAULTS, user_cfg)
    # 本机专属路径/密钥统一走环境变量（.env 已 gitignore，不上传到仓库）。
    # WX_DECRYPTED_DIR 指向本机已导出的数据库目录，属于绝对路径，不该写进公开的 settings.yaml。
    _local_decrypted = os.environ.get("WX_DECRYPTED_DIR", "")
    if _local_decrypted:
        cfg.setdefault("chatlog", {})["decrypted_dir"] = _local_decrypted
    # 环境变量兜底：兼容 DOUBAO_* / DEEPSEEK_* / OPENAI_* / VOLC_* 多种写法
    cfg.setdefault("_env", {})
    cfg["_env"]["api_key"] = (
        os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("DOUBAO_API_KEY")
        or os.environ.get("OPENAI_API_KEY", "")
    )
    cfg["_env"]["base_url"] = (
        os.environ.get("DEEPSEEK_BASE_URL")
        or os.environ.get("DOUBAO_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL", "")
    )
    cfg["_env"]["text_model"] = (
        os.environ.get("DEEPSEEK_TEXT_MODEL")
        or os.environ.get("DOUBAO_TEXT_MODEL")
        or os.environ.get("DOUBAO_TEXT_MODEL", "")
    )
    cfg["_env"]["vision_model"] = os.environ.get("DOUBAO_VISION_MODEL", "")
    return cfg


def load_groups(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """加载 groups.yaml，返回已启用的目标群列表。"""
    target = Path(path) if path else (CONFIG_DIR / "groups.yaml")
    if not target.exists():
        return []

    try:
        data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"groups.yaml 解析失败：{exc}") from exc

    groups = (data or {}).get("target_groups", []) or []
    global_leaders = (data or {}).get("global_leaders", []) or []

    result: List[Dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict) or not group.get("name"):
            continue
        if group.get("enabled") is False:
            continue
        leaders = list(group.get("leaders") or [])
        # 追加全局名单，去重（id 优先，其次 name）
        seen = {_leader_key(l) for l in leaders}
        for gl in global_leaders:
            if _leader_key(gl) not in seen:
                leaders.append(gl)
                seen.add(_leader_key(gl))
        result.append(
            {
                "name": str(group["name"]),
                "leaders": [l for l in leaders if l.get("name") or l.get("id")],
            }
        )
    return result


def _leader_key(leader: Dict[str, Any]) -> str:
    """领导身份的唯一键，用于去重。"""
    if not isinstance(leader, dict):
        return ""
    ident = str(leader.get("id") or "").strip()
    return ident if ident else f"name::{str(leader.get('name') or '').strip()}"


def find_group(group_name: str, path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """按群名查找配置，找不到返回 None（不抛异常，交由上层决定策略）。"""
    for group in load_groups(path):
        if group.get("name") == group_name:
            return group
    return None


def get_leaders(
    group_name: str, path: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """获取某群的领导名单；群未配置时返回空列表。"""
    group = find_group(group_name, path)
    return list(group.get("leaders", [])) if group else []


def load_groups_raw(path: Optional[Path] = None) -> Dict[str, Any]:
    """读取 groups.yaml 原始结构（供控制台「领导设置」面板展示与编辑）。

    与 load_groups 不同，这里返回未经展开的原始字典：
        {"target_groups": [...], "global_leaders": [...]}
    每个群条目保留 name / enabled / leaders(含 id) 原始字段。

    Returns:
        原始配置字典；文件不存在或解析失败时返回空结构。
    """
    target = Path(path) if path else (CONFIG_DIR / "groups.yaml")
    if not target.exists():
        return {"target_groups": [], "global_leaders": []}
    try:
        data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {"target_groups": [], "global_leaders": []}
    if not isinstance(data, dict):
        return {"target_groups": [], "global_leaders": []}
    return {
        "target_groups": list(data.get("target_groups", []) or []),
        "global_leaders": list(data.get("global_leaders", []) or []),
    }


def leaders_as_text(leaders: List[Dict[str, Any]]) -> str:
    """把领导名单渲染成 prompt 里 {leaders_list} 的可读文本。"""
    if not leaders:
        return "（未配置领导名单，请把所有有实指令意味的发言都视为重点）"
    lines = []
    for leader in leaders:
        name = str(leader.get("name") or "").strip()
        ident = str(leader.get("id") or "").strip()
        lines.append(f"- {name}（wxid: {ident}）" if ident else f"- {name}")
    return "\n".join(lines)


def save_group_leaders(
    group_name: str,
    leaders: List[Dict[str, Any]],
    path: Optional[Path] = None,
    global_leaders: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """把某个群的领导名单写回 groups.yaml（保留其他群的配置与字段）。

    设计约定：只增改 `leaders` 字段，其余字段（enabled / id 等）原样保留；
    群名不存在时，追加一个新群条目（默认 enabled）。写入采用「先读全文、
    原位替换、原子落盘」的策略，避免并发写坏 YAML。

    Args:
        group_name: 目标群名。
        leaders: 新的领导名单，形如 `[{"name": "张三", "id": ""}]`。
        path: 群配置文件路径，默认 config/groups.yaml。
        global_leaders: 全局兜底名单（可选，传入则一并写回）。

    Raises:
        ConfigError: YAML 解析失败或写入失败时抛出。
    """
    target = Path(path) if path else (CONFIG_DIR / "groups.yaml")

    if target.exists():
        try:
            data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"groups.yaml 解析失败：{exc}") from exc
    else:
        data = {}

    if not isinstance(data, dict):
        data = {}

    groups = list(data.get("target_groups", []) or [])
    gl = list(global_leaders) if global_leaders is not None else list(data.get("global_leaders", []) or [])

    # 规范化传入的 leaders：只保留有 name 或 id 的条目，去掉空白
    clean_leaders: List[Dict[str, str]] = []
    for leader in leaders or []:
        if not isinstance(leader, dict):
            continue
        name = str(leader.get("name") or "").strip()
        ident = str(leader.get("id") or "").strip()
        if not name and not ident:
            continue
        entry: Dict[str, str] = {"name": name}
        if ident:
            entry["id"] = ident
        clean_leaders.append(entry)

    found = False
    for group in groups:
        if isinstance(group, dict) and group.get("name") == group_name:
            group["leaders"] = clean_leaders
            found = True
            break
    if not found:
        groups.append({"name": group_name, "enabled": True, "leaders": clean_leaders})

    data["target_groups"] = groups
    data["global_leaders"] = gl

    # 原子落盘：先写临时文件再改名，避免写一半崩溃留下坏 YAML
    tmp = target.with_suffix(".yaml.tmp")
    try:
        tmp.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
        tmp.replace(target)
    except OSError as exc:
        raise ConfigError(f"groups.yaml 写入失败：{exc}") from exc


# --------------------------------------------------------------------------- #
# Prompt 模板
# --------------------------------------------------------------------------- #
def load_prompt_template(
    name: str = "leader_brief", extra_vars: Optional[Dict[str, str]] = None
) -> str:
    """加载 config/prompts/<name>.md 的原始内容。

    模板里除 {chat_content} / {leaders_list} 之外的自定义变量可通过 extra_vars 传入。
    """
    target = PROMPT_DIR / f"{name}.md"
    if not target.exists():
        fallback = PROMPT_DIR / "leader_brief.md"
        if fallback.exists():
            target = fallback
        else:
            raise ConfigError(f"prompt 模板不存在：{PROMPT_DIR / (name + '.md')}")
    return target.read_text(encoding="utf-8")


def list_prompt_templates() -> List[str]:
    """列出所有可用 prompt 模板名（不含扩展名）。"""
    if not PROMPT_DIR.exists():
        return []
    return sorted(p.stem for p in PROMPT_DIR.glob("*.md"))
