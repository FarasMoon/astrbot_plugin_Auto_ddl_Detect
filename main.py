"""
DDL Detect Plugin Entry Point
DDL 检测插件入口文件
"""

import re
from datetime import datetime, timedelta
from typing import Optional, Tuple

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import AstrBotConfig
from astrbot.api import logger


# 扩展的 DDL 关键词模式（默认）
DEFAULT_KEYWORDS = ["截止", "截止时间", "截止日期", "deadline", "ddl", "交作业"]


@register("ddldetect", "YourName", "DDL 检测插件 - 自动检测并保存群内 DDL 消息", "1.0.0")
class DDLDetectPlugin(Star):
    """DDL 检测插件主类"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.keywords = self._parse_keywords()
        self.ddl_pattern = self._build_pattern()

    def _parse_keywords(self) -> list:
        """解析 DDL 关键词配置"""
        keywords_str = self.config.get("ddl_keywords", ",".join(DEFAULT_KEYWORDS))
        return [k.strip() for k in keywords_str.split(",") if k.strip()]

    def _build_pattern(self) -> re.Pattern:
        """构建 DDL 检测正则表达式"""
        keyword_pattern = "|".join(re.escape(k) for k in self.keywords)

        # 详细的时间模式
        time_patterns = [
            # 6月10日14点、6月10日14:00、6月10日14点30分
            r"(\d{1,2}月\d{1,2}[日]?(?:\s*[0-2]?\d点?(?:\d{1,2}分?)?)?)",
            # 6-10、6/10
            r"(\d{1,2}[/-]\d{1,2})(?!\d)",
            # 2024年6月10日
            r"(\d{4}年\d{1,2}月\d{1,2}[日]?)",
            # 今天/明天/今晚/今晚19点
            r"(今天|明天|今晚)(?:\s*[0-2]?\d点?(?:\d{1,2}分?)?)?",
            # 周四晚上19点/周四19点/本周四/下周四
            r"((?:本周|下周)?)[一二三四五六日](?:[早晚]?\s*[0-2]?\d点?(?:\d{1,2}分?)?)?",
            # 任意数字时间
            r"(\d{1,2}[时点:]\d{2})",
        ]

        combined_time = "|".join(time_patterns)
        pattern = rf"({keyword_pattern})[：:为]?\s*({combined_time})"
        return re.compile(pattern, re.IGNORECASE)

    def _resolve_relative_time(self, matched_time: str) -> str:
        """解析相对时间（如今天、明天、本周三、周四晚上等）"""
        now = datetime.now()

        # 处理纯相对时间
        if matched_time in ["今天", "明天", "今晚"]:
            if matched_time == "明天":
                return (now + timedelta(days=1)).strftime("%m月%d日")
            return now.strftime("%m月%d日")

        # 处理本周/下周 + 星期
        is_next_week = "下周" in matched_time
        day_map = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6}
        for day_name, day_offset in day_map.items():
            if day_name in matched_time:
                days_ahead = day_offset - now.weekday()
                if is_next_week:
                    days_ahead += 7
                elif days_ahead <= 0:
                    days_ahead += 7
                target = now + timedelta(days=days_ahead)
                return target.strftime("%m月%d日")

        return matched_time

    def _extract_ddl_regex(self, message: str) -> Optional[Tuple[str, str]]:
        """使用正则从消息中提取 DDL"""
        match = self.ddl_pattern.search(message)
        if not match:
            return None

        time_part = match.group(2) if match.lastindex >= 2 else ""
        time_part = self._resolve_relative_time(time_part)
        task_desc = message[:match.start()].strip()

        return task_desc, time_part

    async def _extract_ddl_llm(self, message: str) -> Optional[Tuple[str, str]]:
        """使用 LLM 判断是否为 DDL 消息并提取"""
        try:
            prompt = f"""判断以下消息是否为DDL(截止日期)通知消息。如果是，提取任务内容和截止时间。

消息：{message}

请按以下格式回复：
- 如果是DDL消息：是|任务内容|截止时间
- 如果不是DDL消息：否

示例：
消息：期末论文截止周四晚上19点
回复：是|期末论文|周四晚上19点

消息：今天天气真好
回复：否"""
            
            llm_resp = await self.context.llm_generate(
                chat_provider_id="",
                prompt=prompt,
            )
            
            if not llm_resp:
                return None
            
            result = llm_resp.completion_text.strip()
            if result.startswith("是|"):
                parts = result.split("|")
                if len(parts) >= 3:
                    task = parts[1].strip()
                    time = self._resolve_relative_time(parts[2].strip())
                    return task, time
            return None
        except Exception as e:
            logger.error(f"LLM 检测失败: {e}")
            return None

    async def initialize(self) -> None:
        """插件初始化"""
        mode = self.config.get("detect_mode", "regex")
        logger.info(f"DDL 检测插件已加载，模式: {mode}，关键词: {self.keywords}")

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent) -> MessageEventResult:
        """监听群消息，检测 DDL 格式"""
        message_str = event.message_str.strip()
        detect_mode = self.config.get("detect_mode", "regex")

        # 根据模式选择检测方式
        if detect_mode == "llm":
            ddl_info = await self._extract_ddl_llm(message_str)
        else:
            ddl_info = self._extract_ddl_regex(message_str)

        if not ddl_info:
            return

        task_desc, ddl_time = ddl_info
        group_id = event.message_obj.group_id or "unknown"
        sender_name = event.get_sender_name()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        raw_ddl = {
            "task": task_desc,
            "raw_message": message_str,
            "ddl_time": ddl_time,
            "group_id": group_id,
            "sender": sender_name,
            "detected_at": timestamp,
            "message_id": event.message_obj.message_id
        }

        await self._save_ddl(group_id, raw_ddl)
        logger.info(f"检测到 DDL: {message_str}")

        if self.config.get("enable_auto_reply", True):
            summary = await self._summarize_ddl(raw_ddl) if self.config.get("enable_llm_summary", True) else None
            if summary:
                yield event.plain_result(f"已检测到 DDL：{summary}")

    async def _save_ddl(self, group_id: str, ddl_data: dict) -> None:
        """保存 DDL 到存储"""
        key = f"ddl_{group_id}"
        ddl_list = await self.get_kv_data(key, [])
        ddl_list.append(ddl_data)
        await self.put_kv_data(key, ddl_list)

    async def _summarize_ddl(self, ddl_data: dict) -> Optional[str]:
        """调用 LLM 总结 DDL（不超过15字，无标点）"""
        try:
            prompt = f"""用不超过15个字总结以下DDL，不要带任何标点符号：

任务：{ddl_data.get('task', '未知')}
截止：{ddl_data['ddl_time']}

直接输出总结，不要其他内容。"""
            
            llm_resp = await self.context.llm_generate(
                chat_provider_id="",
                prompt=prompt,
            )
            if not llm_resp:
                return None
            
            # 移除标点符号
            result = llm_resp.completion_text.strip()
            result = re.sub(r'[，。！？、；：""''（）【】《》\s]', '', result)
            return result[:15] if len(result) > 15 else result
        except Exception as e:
            logger.error(f"LLM 总结失败: {e}")
            return None

    @filter.command("ddl")
    async def query_ddl(self, event: AstrMessageEvent) -> MessageEventResult:
        """查询今日保存的 DDL"""
        group_id = event.message_obj.group_id or "unknown"
        key = f"ddl_{group_id}"

        ddl_list = await self.get_kv_data(key, [])
        today = datetime.now().strftime("%Y-%m-%d")
        today_ddls = [
            ddl for ddl in ddl_list
            if ddl.get('detected_at', '').startswith(today)
        ]

        if not today_ddls:
            yield event.plain_result("今日暂无保存的 DDL。")
            return

        result = [f"今日 DDL 共 {len(today_ddls)} 条："]
        for i, ddl in enumerate(today_ddls, 1):
            sender = ddl.get('sender', '未知')
            task = ddl.get('task', '')[:20] or ddl.get('raw_message', '')[:20]
            ddl_time = ddl.get('ddl_time', '未知')
            result.append(f"{i}. {sender} | {task} | {ddl_time}")

        yield event.plain_result("\n".join(result))

    async def terminate(self) -> None:
        """插件销毁时调用"""
        logger.info("DDL 检测插件已卸载")
