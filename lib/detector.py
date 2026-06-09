"""DDL 检测模块：正则匹配 + LLM 语义验证"""

import json
import re
from typing import Optional, Tuple

from astrbot.api import logger

DEFAULT_KEYWORDS = ["截止", "截止时间", "截止日期", "deadline", "ddl", "交作业", "前"]

# 内置时间正则（模块级默认值）
_TIME_PATTERNS = [
    r"\d{1,2}月\d{1,2}[日号]?(?:.*?[0-2]?\d(?:[:：点时])\d{1,2}(?:分?)?)?",
    r"\d{1,2}[/-]\d{1,2}(?!\d)",
    r"\d{4}年\d{1,2}月\d{1,2}[日号]?",
    r"今天|明天|今晚|明晚(?:\s*[0-2]?\d(?:[:：点时])\d{1,2}(?:分?)?)?",
    r"(?:(?:本|下)?周|星期)[一二三四五六日天](?:[早晚]上?\s*[0-2]?\d(?:[:：点时])\d{1,2}(?:分?)?)?",
    r"[早中晚]上?\s*[0-2]?\d(?:[:：点时])\d{1,2}(?:分?)?",
    r"\d{1,2}(?:[:：点时])\d{1,2}(?:分)?",
]


def get_time_patterns(custom_patterns: list | None = None) -> list:
    """返回内置 + 自定义时间正则列表"""
    patterns = list(_TIME_PATTERNS)
    if custom_patterns:
        patterns.extend(custom_patterns)
    return patterns


def build_time_re(custom_patterns: list | None = None) -> re.Pattern:
    """编译时间提取正则（extract_ddl 用）"""
    return re.compile("(" + "|".join(get_time_patterns(custom_patterns)) + ")")


def parse_keywords(keywords_str: str) -> list:
    """解析 DDL 关键词配置"""
    if not keywords_str:
        keywords_str = ",".join(DEFAULT_KEYWORDS)
    return [k.strip() for k in keywords_str.split(",") if k.strip()]


def build_pattern(keywords: list, custom_time_patterns: list | None = None) -> re.Pattern:
    """构建 DDL 检测正则表达式，支持自定义时间模式"""
    keyword_pattern = "|".join(re.escape(k) for k in keywords)

    # 获取包含自定义的时间正则
    time_list = get_time_patterns(custom_time_patterns)

    # 将每个时间模式包裹为捕获组（供副正则提取）
    time_patterns = [
        f"({p})" if not p.startswith("(") else p
        for p in time_list
    ]

    combined_time = "|".join(time_patterns)
    pattern = rf"(({keyword_pattern})[：:为]?\s*({combined_time})|({combined_time})\s*({keyword_pattern}))"
    return re.compile(pattern, re.IGNORECASE)

# LLM 分类 prompt（供调试命令展示）
CLASSIFY_PROMPT = """判断以下群聊消息是否包含一个DDL（截止/限期）任务。不要被"截止"等词语误导——如果是讨论"截止报名"、"截止日期已过"、"生日截止"等非DDL场景，应判定为否。

## 判断标准
- 是DDL：明确给出了任务+时间，且有催促完成的意图（如"请于...前提交"、"最晚...完成"、"ddl是..."）
- 不是DDL：闲聊中提到时间（"周一到期了"）、讨论过去的截止（"昨天截止的"）、只有日期没有任务

## 如果是DDL
提取任务描述和截止时间，以JSON返回：
{"is_ddl": true, "task": "简要任务描述", "ddl_time": "原始时间文本"}

## 如果不是DDL
{"is_ddl": false}

只返回JSON，不要其他文字。消息："""


def extract_ddl(message: str, pattern: re.Pattern, resolve_time_func,
                time_re: re.Pattern | None = None) -> Optional[Tuple[str, str]]:
    """使用正则从消息中提取 DDL。time_re 为空时自动使用默认内置时间正则。"""
    if time_re is None:
        time_re = build_time_re()

    match = pattern.search(message)
    if not match:
        return None

    full_match = match.group(0)
    # 从完整匹配中重新提取时间（避免嵌套捕获组的 group 编号问题）
    tm = time_re.search(full_match)
    if not tm:
        return None
    time_part = resolve_time_func(tm.group(0))

    task_desc = message[:match.start()].strip() + message[match.end():].strip()
    if not task_desc:
        task_desc = message.replace(time_part, "").strip()
    if not task_desc:
        task_desc = "未命名任务"

    return task_desc, time_part


async def classify_ddl(message: str, event, context, provider_id: str | None = None) -> dict | bool | None:
    """LLM 语义验证：判断正则命中的消息是否真的是 DDL。

    返回值：
    - {"task": ..., "ddl_time": ...}  → 确认是DDL，使用此结果
    - False                           → 明确不是DDL（误报），跳过
    - None                            → LLM 不可用/异常，放行（使用正
则结果）
    """
    try:
        if not provider_id:
            provider_id = await context.get_current_chat_provider_id(
                umo=event.unified_msg_origin
            )
        if not provider_id:
            logger.warning("DDL语义验证：未找到可用的LLM")
            return None  # 无LLM可用时放行（不拦截）

        llm_resp = await context.llm_generate(
            chat_provider_id=provider_id,
            prompt=CLASSIFY_PROMPT + message,
        )
        if not llm_resp or not llm_resp.completion_text:
            logger.warning("DDL语义验证：模型返回为空")
            return None

        raw = llm_resp.completion_text.strip()
        # 清洗可能的 markdown 代码块包裹
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        result = json.loads(raw)
        if not isinstance(result, dict):
            return None
        if result.get("is_ddl"):
            return {
                "task": result.get("task", ""),
                "ddl_time": result.get("ddl_time", ""),
            }
        # is_ddl = false → 明确不是DDL，过滤掉
        logger.info(f"DDL语义验证：判断为非DDL（误报过滤）")
        return False
    except json.JSONDecodeError:
        logger.info(f"DDL语义验证：非JSON返回（放行）: {raw[:80]}")
        return None  # parse失败放行，避免拦截正常DDL
    except Exception as e:
        logger.warning(f"DDL语义验证失败（放行）: {e}")
        return None
