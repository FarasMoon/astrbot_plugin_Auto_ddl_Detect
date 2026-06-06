"""AutoDDLDetect - AstrBot 群聊 DDL 自动检测插件"""

import os
import sys
# 确保插件目录在 sys.path 中，使 src.autoddldetect 可导入
_plugin_dir = os.path.dirname(os.path.abspath(__file__))
if _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)

import asyncio
from datetime import datetime, timedelta

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import AstrBotConfig
from astrbot.api import logger

from src.autoddldetect.detector import parse_keywords, build_pattern, extract_ddl
from src.autoddldetect.time_parser import resolve_relative_time
from src.autoddldetect.summarizer import summarize_ddl
from src.autoddldetect.renderer import categorize_ddls, format_text_ddl, render_image_card


# ── 静默监听工具函数 ────────────────────────────────────────

def _should_monitor_group(group_id: str, group_mode: str, group_list_str: str) -> bool:
    """判断群是否应被静默监听"""
    if not group_list_str.strip():
        return group_mode == "blacklist"
    group_ids = [g.strip() for g in group_list_str.split(",") if g.strip()]
    if group_mode == "blacklist":
        return group_id not in group_ids
    return group_id in group_ids


def _format_silent_msg(raw_ddl: dict) -> str:
    """格式化静默监听推送消息"""
    task = raw_ddl.get("summary") or raw_ddl.get("task", raw_ddl.get("raw_message", ""))
    ddl_time = raw_ddl.get("ddl_time", "未知")
    sender = raw_ddl.get("sender", "未知")
    group_id = raw_ddl.get("group_id", "未知")
    detected_at = raw_ddl.get("detected_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return (
        f"[DDL监听]\n"
        f"群: {group_id}\n"
        f"任务: {task}\n"
        f"截止: {ddl_time}\n"
        f"来自: {sender}\n"
        f"时间: {detected_at}"
    )

# 切换命令的临时存储
group_output_format = {}


@register("autoddldetect", "FarasMoon", "DDL 检测插件 - 自动检测并保存群内 DDL 消息", "1.2.0")
class DDLDetectPlugin(Star):
    """DDL 检测插件主类"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.keywords = parse_keywords(config.get("ddl_keywords", ""))
        self.ddl_pattern = build_pattern(self.keywords)
        self.notification_task = None

    # ── 事件处理 ──────────────────────────────────────────────

    async def initialize(self) -> None:
        times_str = self.config.get("notification_times", "08:00")
        self.notification_times = [t.strip() for t in times_str.split(",") if t.strip()]
        logger.info(f"AutoDDLDetect 已加载，关键词: {self.keywords}，通知时间: {self.notification_times}")

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent) -> MessageEventResult:
        """监听群消息，检测 DDL 格式"""
        message_str = event.message_str.strip()
        ddl_info = extract_ddl(message_str, self.ddl_pattern, resolve_relative_time)

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
            if self.config.get("enable_llm_summary", True):
                summary = await summarize_ddl(raw_ddl, event, self.context)
                if summary:
                    yield event.plain_result(f"已检测到 DDL：{summary}")

        # 静默监听模式
        if self.config.get("silent_mode", True):
            silent_admin = self.config.get("silent_admin_sid", "")
            if silent_admin:
                group_mode = self.config.get("silent_group_mode", "blacklist")
                group_list_str = self.config.get("silent_group_list", "")
                if _should_monitor_group(group_id, group_mode, group_list_str):
                    if self.config.get("enable_llm_summary", True):
                        summary = await summarize_ddl(raw_ddl, event, self.context)
                        if summary:
                            raw_ddl["summary"] = summary
                    msg_text = _format_silent_msg(raw_ddl)
                    try:
                        from astrbot.api.star import StarTools
                        import astrbot.api.message_components as Comp
                        from astrbot.api.event import MessageChain
                        chain = MessageChain()
                        chain.chain.append(Comp.Plain(msg_text))
                        await StarTools.send_message_by_id(
                            type="PrivateMessage",
                            id=silent_admin,
                            message_chain=chain,
                            platform=event.get_platform_name(),
                        )
                        logger.info(f"[SilentMonitor] 已推送 DDL 给管理员 {silent_admin}")
                    except Exception as e:
                        logger.error(f"[SilentMonitor] 推送失败: {e}")

    # ── 存储 ──────────────────────────────────────────────────

    async def _save_ddl(self, group_id: str, ddl_data: dict) -> None:
        key = f"ddl_{group_id}"
        ddl_list = await self.get_kv_data(key, [])
        ddl_list.append(ddl_data)
        await self.put_kv_data(key, ddl_list)

    # ── 查询 DDL ──────────────────────────────────────────────

    @filter.command("ddl")
    async def query_ddl(self, event: AstrMessageEvent) -> MessageEventResult:
        """查询今日保存的 DDL"""
        group_id = event.message_obj.group_id or "unknown"
        key = f"ddl_{group_id}"
        ddl_list = await self.get_kv_data(key, [])

        now = datetime.now()
        valid_ddls = []
        removed_count = 0

        for ddl in ddl_list:
            ddl_time_str = ddl.get('ddl_time', '')
            try:
                for fmt in ['%m月%d日', '%m月%d日%H点', '%m月%d日%H:%M']:
                    try:
                        parsed_time = datetime.strptime(ddl_time_str, fmt)
                        parsed_time = parsed_time.replace(year=now.year)
                        if parsed_time < now:
                            removed_count += 1
                        else:
                            valid_ddls.append(ddl)
                        break
                    except ValueError:
                        continue
                else:
                    valid_ddls.append(ddl)
            except Exception:
                valid_ddls.append(ddl)

        if removed_count > 0:
            await self.put_kv_data(key, valid_ddls)

        today = datetime.now().strftime("%Y-%m-%d")
        today_ddls = [ddl for ddl in valid_ddls if ddl.get('detected_at', '').startswith(today)]

        urgent_hours = self.config.get("urgent_hours", 24)
        soon_hours = self.config.get("soon_hours", 48)
        urgent_ddls, soon_ddls, normal_ddls = categorize_ddls(today_ddls, urgent_hours, soon_hours)

        output_format = group_output_format.get(group_id, self.config.get("output_format", "text"))

        # LLM 总结（图片和文字模式都支持）
        if self.config.get("enable_llm_summary", True):
            for ddl in urgent_ddls + soon_ddls + normal_ddls:
                summary = await summarize_ddl(ddl, event, self.context)
                if summary:
                    ddl['summary'] = summary

        if output_format == "image":
            try:
                bg_mode = self.config.get("background_mode", "image")
                bg_value = self.config.get("background_color", "#f0f0f0") if bg_mode == "color" else self.config.get("background_api", "https://t.alcy.cc/moez")
                url = await render_image_card(
                    self, urgent_ddls, soon_ddls, normal_ddls,
                    urgent_hours, soon_hours, bg_mode, bg_value
                )
                yield event.image_result(url)
            except Exception as e:
                logger.error(f"生成图片失败: {e}")
                yield event.plain_result("生成图片失败，以下是文字版：\n" +
                                         format_text_ddl(urgent_ddls, soon_ddls, normal_ddls, urgent_hours, soon_hours))
        else:
            yield event.plain_result(format_text_ddl(urgent_ddls, soon_ddls, normal_ddls, urgent_hours, soon_hours))

    # ── 清除 DDL ──────────────────────────────────────────────

    @filter.command("clearddl", aliases=["清除ddl", "删除ddl"])
    async def clear_ddl(self, event: AstrMessageEvent) -> MessageEventResult:
        """清除今日保存的 DDL"""
        group_id = event.message_obj.group_id or "unknown"
        key = f"ddl_{group_id}"
        ddl_list = await self.get_kv_data(key, [])
        today = datetime.now().strftime("%Y-%m-%d")
        remaining_ddls = [ddl for ddl in ddl_list if not ddl.get('detected_at', '').startswith(today)]

        if len(ddl_list) > len(remaining_ddls):
            await self.put_kv_data(key, remaining_ddls)
            count = len(ddl_list) - len(remaining_ddls)
            yield event.plain_result(f"✅ 已清除今日的 {count} 条 DDL 记录")
        else:
            yield event.plain_result("📭 今日暂无 DDL 记录可清除")

    # ── 切换输出格式 ──────────────────────────────────────────

    @filter.command("ddl_image")
    async def switch_to_image(self, event: AstrMessageEvent) -> MessageEventResult:
        group_id = event.message_obj.group_id or "unknown"
        group_output_format[group_id] = "image"
        yield event.plain_result("✅ 已切换到图片输出模式")

    @filter.command("ddl_text")
    async def switch_to_text(self, event: AstrMessageEvent) -> MessageEventResult:
        group_id = event.message_obj.group_id or "unknown"
        group_output_format[group_id] = "text"
        yield event.plain_result("✅ 已切换到文字输出模式")

    # ── 测试 ──────────────────────────────────────────────────

    @filter.command("ddl_test")
    async def test_notification(self, event: AstrMessageEvent) -> MessageEventResult:
        """测试定时通知"""
        group_id = event.message_obj.group_id or "unknown"
        key = f"ddl_{group_id}"
        ddl_list = await self.get_kv_data(key, [])
        today = datetime.now().strftime("%Y-%m-%d")
        today_ddls = [ddl for ddl in ddl_list if ddl.get('detected_at', '').startswith(today)]

        if not today_ddls:
            yield event.plain_result("今日暂无 DDL 可测试")
            return

        urgent_hours = self.config.get("urgent_hours", 24)
        soon_hours = self.config.get("soon_hours", 48)
        urgent_ddls, soon_ddls, normal_ddls = categorize_ddls(today_ddls, urgent_hours, soon_hours)

        output_format = group_output_format.get(group_id, self.config.get("output_format", "text"))

        # LLM 总结（图片和文字模式都支持）
        if self.config.get("enable_llm_summary", True):
            for ddl in urgent_ddls + soon_ddls + normal_ddls:
                summary = await summarize_ddl(ddl, event, self.context)
                if summary:
                    ddl['summary'] = summary

        if output_format == "image":
            try:
                bg_mode = self.config.get("background_mode", "image")
                bg_value = self.config.get("background_color", "#f0f0f0") if bg_mode == "color" else self.config.get("background_api", "https://t.alcy.cc/moez")
                url = await render_image_card(
                    self, urgent_ddls, soon_ddls, normal_ddls,
                    urgent_hours, soon_hours, bg_mode, bg_value
                )
                yield event.image_result(url)
            except Exception as e:
                yield event.plain_result(f"生成测试图片失败: {e}")
        else:
            yield event.plain_result(format_text_ddl(urgent_ddls, soon_ddls, normal_ddls, urgent_hours, soon_hours))

    # ── 销毁 ──────────────────────────────────────────────────

    async def terminate(self) -> None:
        if self.notification_task and not self.notification_task.done():
            self.notification_task.cancel()
            try:
                await self.notification_task
            except asyncio.CancelledError:
                pass
        logger.info("AutoDDLDetect 已卸载")
