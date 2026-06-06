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

# 相对时间映射
RELATIVE_TIME_MAP = {
    "今天": datetime.now(),
    "明天": datetime.now() + timedelta(days=1),
    "今晚": datetime.now(),
    "明天晚上": datetime.now() + timedelta(days=1),
}


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
            # 今天/明天/今晚 14点
            r"(今天|明天|今晚)(?:\s*[0-2]?\d点?(?:\d{1,2}分?)?)?",
            # 本周五、下周三
            r"(本周|下周)[一二三四五六日]",
            # 任意数字时间
            r"(\d{1,2}[时点:]\d{2})",
        ]

        combined_time = "|".join(time_patterns)
        pattern = rf"({keyword_pattern})[：:为]?\s*({combined_time})"
        return re.compile(pattern, re.IGNORECASE)

    def _resolve_relative_time(self, matched_time: str) -> str:
        """解析相对时间（如今天、明天、本周三等）"""
        now = datetime.now()

        if matched_time in ["今天"]:
            return now.strftime("%m月%d日")
        if matched_time in ["明天"]:
            return (now + timedelta(days=1)).strftime("%m月%d日")
        if matched_time in ["今晚"]:
            return now.strftime("%m月%d日")

        # 处理本周/下周
        if "本周" in matched_time or "下周" in matched_time:
            day_map = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6}
            for day_name, day_offset in day_map.items():
                if day_name in matched_time:
                    is_next_week = "下周" in matched_time
                    days_ahead = day_offset - now.weekday()
                    if is_next_week:
                        days_ahead += 7
                    elif days_ahead < 0:
                        days_ahead += 7
                    target = now + timedelta(days=days_ahead)
                    return target.strftime("%m月%d日")

        return matched_time

    def _extract_ddl(self, message: str) -> Optional[Tuple[str, str]]:
        """
        从消息中提取 DDL

        Returns:
            (任务描述, 截止时间) 或 None
        """
        match = self.ddl_pattern.search(message)
        if not match:
            return None

        keyword = match.group(1)
        time_part = match.group(2) if match.lastindex >= 2 else ""

        # 解析相对时间
        time_part = self._resolve_relative_time(time_part)

        # 提取任务描述（截止时间之前的内容）
        task_end = match.start(2) if match.lastindex >= 2 else match.end()
        task_desc = message[:match.start()].strip()

        return task_desc, time_part

    async def initialize(self) -> None:
        """插件初始化"""
        logger.info(f"DDL 检测插件已加载，关键词: {self.keywords}")

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent) -> MessageEventResult:
        """监听群消息，检测 DDL 格式"""
        message_str = event.message_str.strip()

        ddl_info = self._extract_ddl(message_str)
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

        # 检查是否启用自动回复
        if self.config.get("enable_auto_reply", True):
            summary = await self._summarize_ddl(raw_ddl) if self.config.get("enable_llm_summary", True) else None
            if summary:
                yield event.plain_result(f"已检测到 DDL 并保存：\n{summary}")

    async def _save_ddl(self, group_id: str, ddl_data: dict) -> None:
        """保存 DDL 到存储"""
        key = f"ddl_{group_id}"
        ddl_list = await self.get_kv_data(key, [])
        ddl_list.append(ddl_data)
        await self.put_kv_data(key, ddl_list)

    async def _summarize_ddl(self, ddl_data: dict) -> Optional[str]:
        """调用 LLM 总结 DDL"""
        try:
            prompt = self._build_summary_prompt(ddl_data)
            llm_resp = await self.context.llm_generate(
                chat_provider_id="",
                prompt=prompt,
            )
            return llm_resp.completion_text.strip() if llm_resp else None
        except Exception as e:
            logger.error(f"LLM 总结失败: {e}")
            return None

    def _build_summary_prompt(self, ddl_data: dict) -> str:
        """构建 LLM 总结提示词"""
        return f"""请帮我总结以下 DDL 信息，提取关键内容：

任务描述：{ddl_data.get('task', '未知')}
原始消息：{ddl_data['raw_message']}
截止时间：{ddl_data['ddl_time']}
发送者：{ddl_data['sender']}
检测时间：{ddl_data['detected_at']}

请用一句话总结这个 DDL，包含任务内容和截止时间。"""

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

        result = [f"今日 DDL 共 {len(today_ddls)} 条：\n"]
        for i, ddl in enumerate(today_ddls, 1):
            ddl_time = ddl.get('ddl_time', '未知')
            sender = ddl.get('sender', '未知')
            msg_preview = ddl.get('raw_message', '')[:50]
            result.append(
                f"{i}. {ddl_time} - {sender}\n"
                f"   {msg_preview}..."
            )

        yield event.plain_result("\n".join(result))

    async def terminate(self) -> None:
        """插件销毁时调用"""
        logger.info("DDL 检测插件已卸载")
