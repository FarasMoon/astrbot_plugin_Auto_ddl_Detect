"""AutoDDLDetect - AstrBot 群聊 DDL 自动检测插件"""

import os
import sys
# 确保插件目录在 sys.path 中，使 lib 可导入
_plugin_dir = os.path.dirname(os.path.abspath(__file__))
if _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)

import asyncio
from datetime import datetime, timedelta

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import AstrBotConfig
from astrbot.api import logger

from lib.detector import parse_keywords, build_pattern, extract_ddl
from lib.time_parser import resolve_relative_time
from lib.summarizer import summarize_ddl
from lib.renderer import categorize_ddls, format_text_ddl, render_image_card


# ── 静默监听工具函数 ────────────────────────────────────────

def _should_monitor_group(group_id: str, silent_whitelist: bool, group_list_str: str) -> bool:
    """判断群是否应被静默监听"""
    if not group_list_str.strip():
        return not silent_whitelist
    group_ids = [g.strip() for g in group_list_str.split(",") if g.strip()]
    if silent_whitelist:
        return group_id in group_ids
    return group_id not in group_ids


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


# ── DDL 过期清理 ────────────────────────────────────────────

def _clean_expired_ddls(ddl_list: list, now: datetime) -> tuple:
    """清理已过期的 DDL，返回 (有效列表, 清理数量)"""
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
    return valid_ddls, removed_count


def _filter_today(ddls: list) -> list:
    """筛选今天的 DDL"""
    today = datetime.now().strftime("%Y-%m-%d")
    return [ddl for ddl in ddls if ddl.get('detected_at', '').startswith(today)]


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
        self.monitored_groups: set = set()
        self.admin_ids: list = []

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        """检查消息发送者是否为管理员"""
        if not self.admin_ids:
            return False
        sender_id = event.message_obj.sender.user_id if event.message_obj.sender else ""
        return sender_id in self.admin_ids

    # ── 事件处理 ──────────────────────────────────────────────

    async def initialize(self) -> None:
        times_str = self.config.get("notification_times", "08:00")
        self.notification_times = [t.strip() for t in times_str.split(",") if t.strip()]
        admin_str = self.config.get("silent_admin_sid", "")
        self.admin_ids = [a.strip() for a in admin_str.split(",") if a.strip()]
        # 群名映射
        self._group_names = self._load_group_names()
        # 截止提醒追踪
        self._reminded_ddls: set = set()
        # 启动截止前提醒后台任务
        self._reminder_task = asyncio.ensure_future(self._deadline_reminder_loop())
        logger.info(f"AutoDDLDetect 已加载，关键词: {self.keywords}，通知时间: {self.notification_times}，管理员: {self.admin_ids}")

    def _load_group_names(self) -> dict:
        raw = self.config.get("group_display", "")
        if not raw.strip():
            return {}
        try:
            import json
            return json.loads(raw)
        except Exception:
            logger.warning(f"群名称映射 JSON 解析失败: {raw}")
            return {}

    def _get_group_label(self, group_id: str) -> str:
        name = self._group_names.get(group_id, "")
        if name:
            return f"{name}({group_id})"
        return group_id

    def _build_source_info(self, ddls: list) -> str:
        """根据 DDL 列表构建来源信息"""
        gids = sorted(set(d.get('group_id', '') for d in ddls if d.get('group_id')))
        if not gids:
            return ""
        labels = [self._get_group_label(g) for g in gids]
        return "来源: " + "、".join(labels)

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
        self.monitored_groups.add(group_id)
        logger.info(f"检测到 DDL: {message_str}")

        if self.config.get("enable_auto_reply", True):
            if self.config.get("enable_llm_summary", True):
                summary = await summarize_ddl(raw_ddl, event, self.context)
                if summary:
                    yield event.plain_result(f"已检测到 DDL：{summary}")

        # 静默监听模式：跨平台推送给所有管理员
        if self.config.get("silent_mode", True) and self.admin_ids:
            silent_whitelist = self.config.get("silent_whitelist", False)
            group_list_str = self.config.get("silent_group_list", "")
            if _should_monitor_group(group_id, silent_whitelist, group_list_str):
                if self.config.get("enable_llm_summary", True):
                    summary = await summarize_ddl(raw_ddl, event, self.context)
                    if summary:
                        raw_ddl["summary"] = summary
                msg_text = _format_silent_msg(raw_ddl)
                from astrbot.api.star import StarTools
                import astrbot.api.message_components as Comp
                from astrbot.api.event import MessageChain
                platform = event.get_platform_name()
                for admin_id in self.admin_ids:
                    try:
                        chain = MessageChain()
                        chain.chain.append(Comp.Plain(msg_text))
                        admin_session = f"{platform}:FriendMessage:{admin_id}"
                        await StarTools.send_message(admin_session, chain)
                        logger.info(f"[SilentMonitor] 已推送 DDL 给管理员 {admin_id}")
                    except Exception as e:
                        logger.error(f"[SilentMonitor] 推送给 {admin_id} 失败: {e}")

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
        group_id = event.message_obj.group_id

        # 私聊：检查是否为管理员
        if not group_id:
            if self._is_admin(event):
                result = await self._query_all_groups_ddl(event)
                if isinstance(result, tuple):
                    mode, content = result
                    if mode == "image":
                        yield event.image_result(content)
                    else:
                        yield event.plain_result(content)
                else:
                    yield event.plain_result(result)
                return
            yield event.plain_result("📭 私聊仅管理员(silent_admin_sid)可查看汇总")
            return

        # 群聊：查本群 DDL
        key = f"ddl_{group_id}"
        result = await self._query_single_group(event, group_id, key)
        if isinstance(result, tuple):
            mode, content = result
            if mode == "image":
                yield event.image_result(content)
            else:
                yield event.plain_result(content)
        else:
            yield event.plain_result(result)

    async def _query_single_group(self, event, group_id, key):
        """查询并格式化单个群的 DDL"""
        ddl_list = await self.get_kv_data(key, [])
        now = datetime.now()
        valid_ddls, removed_count = _clean_expired_ddls(ddl_list, now)
        if removed_count > 0:
            await self.put_kv_data(key, valid_ddls)

        today_ddls = _filter_today(valid_ddls)
        if not today_ddls:
            return "📭 今日暂无 DDL 记录"

        return await self._format_ddl_output(event, group_id, today_ddls)

    async def _query_all_groups_ddl(self, event):
        """汇总所有监听群的 DDL（管理员专用），归并到一张卡片"""
        groups = sorted(self.monitored_groups)
        if not groups:
            return "📭 暂无监听的群组"

        silent_whitelist = self.config.get("silent_whitelist", False)
        group_list_str = self.config.get("silent_group_list", "")
        all_today_ddls = []

        for gid in groups:
            if not _should_monitor_group(gid, silent_whitelist, group_list_str):
                continue

            key = f"ddl_{gid}"
            ddl_list = await self.get_kv_data(key, [])
            now = datetime.now()
            valid_ddls, removed_count = _clean_expired_ddls(ddl_list, now)
            if removed_count > 0:
                await self.put_kv_data(key, valid_ddls)

            today_ddls = _filter_today(valid_ddls)
            for ddl in today_ddls:
                ddl["group_id"] = gid
            all_today_ddls.extend(today_ddls)

        if not all_today_ddls:
            return "📭 所有监听群今日暂无 DDL 记录"

        # 归并所有群的 DDL，渲染单张卡片
        merged_id = "__admin_all_groups__"
        return await self._format_ddl_output(event, merged_id, all_today_ddls)

    async def _format_ddl_output(self, event, group_id, today_ddls):
        """格式化单个群的 DDL 输出，返回 (type, content)"""
        urgent_hours = self.config.get("urgent_hours", 24)
        soon_hours = self.config.get("soon_hours", 48)
        urgent_ddls, soon_ddls, normal_ddls = categorize_ddls(today_ddls, urgent_hours, soon_hours)

        if self.config.get("enable_llm_summary", True):
            for ddl in urgent_ddls + soon_ddls + normal_ddls:
                summary = await summarize_ddl(ddl, event, self.context)
                if summary:
                    ddl['summary'] = summary

        output_format = group_output_format.get(
            group_id,
            "image" if self.config.get("output_as_image", True) else "text"
        )
        source_info = ""
        if group_id == "__admin_all_groups__":
            source_info = self._build_source_info(today_ddls)
        elif group_id != "unknown":
            source_info = f"本群: {self._get_group_label(group_id)}"

        if output_format == "image":
            try:
                bg_as_image = self.config.get("background_as_image", True)
                bg_value = self.config.get("background_color", "#f0f0f0") if not bg_as_image else self.config.get("background_api", "https://t.alcy.cc/moez")
                bg_mode = "image" if bg_as_image else "color"
                url = await render_image_card(
                    self, urgent_ddls, soon_ddls, normal_ddls,
                    urgent_hours, soon_hours, bg_mode, bg_value,
                    source_info=source_info
                )
                return ("image", url)
            except Exception as e:
                logger.error(f"生成图片失败: {e}")
        return ("text", format_text_ddl(urgent_ddls, soon_ddls, normal_ddls,
                                         urgent_hours, soon_hours, source_info))

    # ── 清除 DDL ──────────────────────────────────────────────

    @filter.command("clearddl", aliases=["清除ddl"])
    async def clear_ddl(self, event: AstrMessageEvent) -> MessageEventResult:
        """清除当前群聊/用户的 DDL；静默监听模式下管理员清除所有"""
        group_id = event.message_obj.group_id

        # 静默监听模式 + 管理员（群聊或私聊）→ 清除所有群的 DDL
        if self.config.get("silent_mode", True) and self._is_admin(event):
            yield event.plain_result(await self._clear_all_groups_ddl())
            return

        # 普通用户：清除当前群/私聊的 DDL
        gid = group_id or "unknown"
        key = f"ddl_{gid}"
        ddl_list = await self.get_kv_data(key, [])
        today = datetime.now().strftime("%Y-%m-%d")
        remaining_ddls = [ddl for ddl in ddl_list if not ddl.get('detected_at', '').startswith(today)]

        if len(ddl_list) > len(remaining_ddls):
            await self.put_kv_data(key, remaining_ddls)
            count = len(ddl_list) - len(remaining_ddls)
            yield event.plain_result(f"✅ 已清除今日的 {count} 条 DDL 记录")
        else:
            yield event.plain_result("📭 今日暂无 DDL 记录可清除")

    @filter.command("清除所有ddl")
    async def clear_all_ddl(self, event: AstrMessageEvent) -> MessageEventResult:
        """清除所有缓存的 DDL（仅管理员）"""
        if not self._is_admin(event):
            yield event.plain_result("❌ 仅管理员可清除所有 DDL")
            return
        yield event.plain_result(await self._clear_all_groups_ddl())

    async def _clear_all_groups_ddl(self) -> str:
        """清除所有已监听群的 DDL 数据"""
        if not self.monitored_groups:
            return "📭 暂无监听的群组数据"

        total_removed = 0
        for gid in list(self.monitored_groups):
            key = f"ddl_{gid}"
            await self.put_kv_data(key, [])
            total_removed += 1

        self.monitored_groups.clear()
        return f"✅ 已清除 {total_removed} 个群的全部 DDL 记录"

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

        output_format = group_output_format.get(
            group_id,
            "image" if self.config.get("output_as_image", True) else "text"
        )

        if self.config.get("enable_llm_summary", True):
            for ddl in urgent_ddls + soon_ddls + normal_ddls:
                summary = await summarize_ddl(ddl, event, self.context)
                if summary:
                    ddl['summary'] = summary

        if output_format == "image":
            try:
                bg_as_image = self.config.get("background_as_image", True)
                bg_value = self.config.get("background_color", "#f0f0f0") if not bg_as_image else self.config.get("background_api", "https://t.alcy.cc/moez")
                bg_mode = "image" if bg_as_image else "color"
                url = await render_image_card(
                    self, urgent_ddls, soon_ddls, normal_ddls,
                    urgent_hours, soon_hours, bg_mode, bg_value
                )
                yield event.image_result(url)
            except Exception as e:
                yield event.plain_result(f"生成测试图片失败: {e}")
        else:
            yield event.plain_result(format_text_ddl(urgent_ddls, soon_ddls, normal_ddls, urgent_hours, soon_hours))

    @filter.command("ddl_remind_test")
    async def test_reminder(self, event: AstrMessageEvent) -> MessageEventResult:
        """手动触发截止前提醒测试"""
        remind_hours = self.config.get("deadline_remind_hours", 6)
        if remind_hours <= 0 or not self.config.get("deadline_remind_enabled", True):
            yield event.plain_result("⚠️ 截止前提醒未启用或 remind_hours <= 0")
            return
        if not self.admin_ids:
            yield event.plain_result("⚠️ 未配置管理员(silent_admin_sid)，无法发送提醒")
            return
        if not self.monitored_groups:
            yield event.plain_result("📭 暂无监听的群组")
            return

        # 临时清空去重集，允许再次提醒
        self._reminded_ddls.clear()

        persona_id = self.config.get("deadline_remind_persona", "")
        persona_note = f"（人格: {persona_id}）" if persona_id else "（使用默认人格）"
        yield event.plain_result(f"🔄 强制触发提醒{persona_note}，开始生成...")

        sent, total, skip_t, skip_p = await self._check_deadline_reminders(remind_hours, force=True)
        if sent > 0:
            yield event.plain_result(f"✅ 已发送 {sent} 条提醒，请查看管理员私聊")
        else:
            yield event.plain_result(
                f"⚠️ 未发送任何提醒（共检查 {total} 条 DDL，"
                f"解析失败 {skip_p} 条）"
            )

    @filter.command("ddl_personas")
    async def list_personas(self, event: AstrMessageEvent) -> MessageEventResult:
        """列出 AstrBot 中可用的人格列表"""
        try:
            persona_mgr = self.context.persona_manager
            personas = await persona_mgr.get_all_personas()
            if not personas:
                yield event.plain_result("📭 当前无可用人格，请在 AstrBot 人格设置中创建")
                return

            lines = ["📋 可用人格列表（填入 deadline_remind_persona）："]
            for p in personas:
                pid = getattr(p, 'persona_id', '?')
                lines.append(f"  - {pid}")
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            yield event.plain_result(f"获取人格列表失败: {e}")

    # ── 销毁 ──────────────────────────────────────────────────

    async def terminate(self) -> None:
        if self.notification_task and not self.notification_task.done():
            self.notification_task.cancel()
            try:
                await self.notification_task
            except asyncio.CancelledError:
                pass
        if self._reminder_task and not self._reminder_task.done():
            self._reminder_task.cancel()
            try:
                await self._reminder_task
            except asyncio.CancelledError:
                pass
        logger.info("AutoDDLDetect 已卸载")

    # ── 截止前提醒 ────────────────────────────────────────────

    async def _deadline_reminder_loop(self):
        """后台循环，每隔 5 分钟检查是否有 DDL 即将截止"""
        await asyncio.sleep(10)  # 启动后等 10 秒再开始
        while True:
            try:
                remind_hours = self.config.get("deadline_remind_hours", 6)
                if remind_hours <= 0:
                    await asyncio.sleep(300)
                    continue
                if not self.config.get("deadline_remind_enabled", True):
                    await asyncio.sleep(300)
                    continue
                if not self.admin_ids:
                    await asyncio.sleep(300)
                    continue

                await self._check_deadline_reminders(remind_hours)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[DeadlineReminder] 循环异常: {e}")
            await asyncio.sleep(300)  # 每 5 分钟检查一次

    async def _check_deadline_reminders(self, remind_hours: float, force: bool = False):
        """检查所有监听群的 DDL，提醒即将截止的。force=True 时忽略时间窗口，强制发送第一条。
        返回 (发送数, 总数, 过滤数)"""
        from astrbot.api.star import StarTools
        import astrbot.api.message_components as Comp
        from astrbot.api.event import MessageChain
        from lib.time_parser import parse_ddl_time as _parse_time

        now = datetime.now()
        notified_count = 0
        total_checked = 0
        skipped_time = 0
        skipped_parse = 0

        for gid in list(self.monitored_groups):
            silent_whitelist = self.config.get("silent_whitelist", False)
            group_list_str = self.config.get("silent_group_list", "")
            if not _should_monitor_group(gid, silent_whitelist, group_list_str):
                continue

            key = f"ddl_{gid}"
            ddl_list = await self.get_kv_data(key, [])
            valid_ddls, _ = _clean_expired_ddls(ddl_list, now)

            for ddl in valid_ddls:
                total_checked += 1
                ddl_time_str = ddl.get('ddl_time', '')

                if force:
                    # 强制模式：跳过时间窗口和去重，仅取第一条有效 DDL
                    if notified_count >= 1:
                        continue
                else:
                    deadline = _parse_time(ddl_time_str)
                    if not deadline:
                        skipped_parse += 1
                        continue

                    remaining = (deadline - now).total_seconds() / 3600
                    if remaining < 0 or remaining > remind_hours:
                        skipped_time += 1
                        continue

                    # 去重
                    dedup_key = (gid, ddl.get('detected_at', ''), ddl_time_str)
                    if dedup_key in self._reminded_ddls:
                        continue
                    self._reminded_ddls.add(dedup_key)

                deadline = _parse_time(ddl_time_str) if force else deadline
                remaining = max(0, ((deadline - now).total_seconds() / 3600) if deadline else 0)

                # 构建提醒
                group_label = self._get_group_label(gid)
                task = ddl.get('summary') or ddl.get('task', ddl.get('raw_message', ''))
                sender = ddl.get('sender', '未知')
                hours_left = max(0, round(remaining * 10) / 10)

                # 尝试用人格 + LLM 生成提醒
                persona_prompt = await self._get_persona_prompt()
                if persona_prompt:
                    try:
                        provider_id = await self.context.get_current_chat_provider_id(
                            umo=f"__ddl_reminder__:{gid}"
                        )
                        if not provider_id:
                            provider_id = await self.context.get_current_chat_provider_id(
                                umo="__ddl_reminder__"
                            )
                    except Exception:
                        provider_id = None

                    if provider_id:
                        try:
                            remind_prompt = (
                                f"{persona_prompt}\n\n"
                                f"现在需要提醒：来自群「{group_label}」的 {sender} "
                                f"有一个任务「{task}」将在 {hours_left} 小时后截止（{ddl_time_str}）。"
                                f"请用你的语气生成一条简洁的提醒消息（不超过100字）。"
                            )
                            llm_resp = await self.context.llm_generate(
                                chat_provider_id=provider_id,
                                prompt=remind_prompt,
                            )
                            if llm_resp and llm_resp.completion_text:
                                msg_text = llm_resp.completion_text.strip()
                            else:
                                msg_text = f"⏰ 提醒：群「{group_label}」中 {sender} 的任务「{task}」将在 {hours_left} 小时后截止（{ddl_time_str}）"
                        except Exception as e:
                            logger.warning(f"[DeadlineReminder] LLM 生成失败: {e}")
                            msg_text = f"⏰ 提醒：群「{group_label}」中 {sender} 的任务「{task}」将在 {hours_left} 小时后截止（{ddl_time_str}）"
                    else:
                        msg_text = f"⏰ 提醒：群「{group_label}」中 {sender} 的任务「{task}」将在 {hours_left} 小时后截止（{ddl_time_str}）"
                else:
                    msg_text = f"⏰ 提醒：群「{group_label}」中 {sender} 的任务「{task}」将在 {hours_left} 小时后截止（{ddl_time_str}）"

                # 推送给所有管理员
                for admin_id in self.admin_ids:
                    try:
                        chain = MessageChain()
                        chain.chain.append(Comp.Plain(msg_text))
                        # 尝试多个常见平台前缀
                        sent_ok = False
                        for prefix in ("aiocqhttp", "wechat", "qq"):
                            try:
                                admin_session = f"{prefix}:FriendMessage:{admin_id}"
                                await StarTools.send_message(admin_session, chain)
                                sent_ok = True
                                break
                            except Exception:
                                continue
                        if sent_ok:
                            notified_count += 1
                        else:
                            logger.warning(f"[DeadlineReminder] 所有平台前缀均无法向 {admin_id} 发送")
                    except Exception as e:
                        logger.warning(f"[DeadlineReminder] 发送失败: {e}")

        if notified_count > 0:
            logger.info(f"[DeadlineReminder] 已推送 {notified_count} 条截止提醒")
        else:
            logger.info(f"[DeadlineReminder] 检查 {total_checked} 条 DDL，均不在截止窗口内（解析失败 {skipped_parse}，时间不匹配 {skipped_time}）")

        return (notified_count, total_checked, skipped_time, skipped_parse)

    async def _get_persona_prompt(self) -> str:
        """获取截止提醒人格提示词"""
        persona_id = self.config.get("deadline_remind_persona", "")
        try:
            if persona_id:
                persona = await self.context.persona_manager.get_persona(persona_id)
                if persona and persona.system_prompt:
                    return persona.system_prompt
            # 回退到默认人格
            default = await self.context.persona_manager.get_default_persona_v3(None)
            if default and default.prompt:
                return default.prompt
        except Exception as e:
            logger.warning(f"[DeadlineReminder] 获取人格失败: {e}")
        return ""
