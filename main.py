"""AutoDDLDetect - AstrBot 群聊 DDL 自动检测插件"""

import os
import sys
# 确保插件目录在 sys.path 中，使 lib 可导入
_plugin_dir = os.path.dirname(os.path.abspath(__file__))
if _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)

import asyncio
import json
from datetime import datetime

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import AstrBotConfig
from astrbot.api import logger

from lib.detector import parse_keywords, build_pattern, extract_ddl, classify_ddl, CLASSIFY_PROMPT
from lib.time_parser import resolve_relative_time, parse_ddl_time
from lib.summarizer import summarize_ddl
from lib.renderer import categorize_ddls, format_text_ddl, render_image_card
from lib.monitor import should_monitor_group, format_silent_msg
from lib.storage import MAX_DDL_PER_GROUP, clean_expired_ddls, filter_today


# ── 切换命令的临时存储 ────────────────────────────────────────
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
        self._seen_messages: set = set()  # (group_id, message_id) 去重
        self._msg_lock = asyncio.Lock()   # 保护 _seen_messages 并发
        self._summary_locks: dict = {}    # per-group 锁，防 summary 写回竞态

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
        # 截止提醒追踪（从 KV 恢复以跨重启持久化）
        stored = await self.get_kv_data("__reminded_ddls", [])
        self._reminded_ddls: set = set(tuple(x) for x in stored) if stored else set()
        # 启动截止前提醒后台任务
        self._reminder_task = asyncio.ensure_future(self._deadline_reminder_loop())
        logger.info(f"AutoDDLDetect 已加载，关键词: {self.keywords}，通知时间: {self.notification_times}，管理员: {self.admin_ids}")

    def _get_summary_lock(self, group_id: str) -> asyncio.Lock:
        """获取 per-group 锁（懒创建）"""
        if group_id not in self._summary_locks:
            self._summary_locks[group_id] = asyncio.Lock()
        return self._summary_locks[group_id]

    def _load_group_names(self) -> dict:
        raw = self.config.get("group_display", "")
        if not raw.strip():
            return {}
        try:
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

    def _get_admin_sessions(self, admin_id: str) -> list:
        """解析 admin_id 得到有效的消息 session 列表"""
        if ":" in admin_id:
            platform, uid = admin_id.split(":", 1)
            return [f"{platform}:FriendMessage:{uid}"]
        sessions = []
        try:
            for inst in self.context.platform_manager.platform_insts:
                pid = inst.meta().id
                sessions.append(f"{pid}:FriendMessage:{admin_id}")
        except Exception:
            pass
        return sessions

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent) -> MessageEventResult:
        """监听群消息，检测 DDL 格式"""
        message_str = event.message_str.strip()
        ddl_info = extract_ddl(message_str, self.ddl_pattern, resolve_relative_time)

        if not ddl_info:
            return

        task_desc, ddl_time = ddl_info
        group_id = event.message_obj.group_id or "unknown"
        msg_id = event.message_obj.message_id

        # 消息级去重
        async with self._msg_lock:
            dedup_key = (group_id, msg_id)
            if dedup_key in self._seen_messages:
                return
            self._seen_messages.add(dedup_key)
            if len(self._seen_messages) > 10000:
                self._seen_messages.clear()

        # 正则+LLM 模式：语义验证过滤误报
        if self.config.get("ddl_detect_mode", "仅正则") == "正则+LLM 验证":
            provider_id = self.config.get("ddl_llm_provider", "") or None
            verified = await classify_ddl(message_str, event, self.context, provider_id)
            if verified is False:
                return  # LLM 明确判断不是 DDL，跳过
            if verified is not None:
                # LLM 返回了更精准的 task/ddl_time
                task_desc = verified["task"] or task_desc
                ddl_time = verified["ddl_time"] or ddl_time
                if verified["ddl_time"]:
                    ddl_time = resolve_relative_time(verified["ddl_time"])

        sender_name = event.get_sender_name()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        raw_ddl = {
            "task": task_desc,
            "raw_message": message_str,
            "ddl_time": ddl_time,
            "group_id": group_id,
            "sender": sender_name,
            "detected_at": timestamp,
            "message_id": msg_id
        }

        await self._save_ddl(group_id, raw_ddl)
        self.monitored_groups.add(group_id)
        logger.info(f"检测到 DDL: {message_str}")

        # LLM 总结（仅调用一次，结果缓存到 KV）
        summary = await self._summarize_ddl_cached(group_id, raw_ddl, event)

        if self.config.get("enable_auto_reply", False):
            if summary:
                yield event.plain_result(f"已检测到 DDL：{summary}")

        # 静默监听模式：跨平台推送给所有管理员
        if self.config.get("silent_mode", True) and self.admin_ids:
            silent_whitelist = self.config.get("silent_whitelist", False)
            group_list_str = self.config.get("silent_group_list", "")
            if should_monitor_group(group_id, silent_whitelist, group_list_str):
                if summary:
                    raw_ddl["summary"] = summary
                msg_text = format_silent_msg(raw_ddl)
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
        """保存 DDL（保存前清理过期 + KV 上限保护）"""
        key = f"ddl_{group_id}"
        lock = self._get_summary_lock(group_id)
        async with lock:
            ddl_list = await self.get_kv_data(key, [])
            # 保存前清理过期
            now = datetime.now()
            ddl_list, removed = clean_expired_ddls(ddl_list, now)
            ddl_list.append(ddl_data)
            # 每群最多保留 MAX_DDL_PER_GROUP 条
            if len(ddl_list) > MAX_DDL_PER_GROUP:
                ddl_list = ddl_list[-MAX_DDL_PER_GROUP:]
            await self.put_kv_data(key, ddl_list)

    async def _summarize_ddl_cached(self, group_id: str, raw_ddl: dict,
                                     event) -> str | None:
        """带缓存的 LLM 总结：已有 summary 则跳过，否则调 LLM 并回存 KV（加锁防竞态）"""
        if raw_ddl.get("summary"):
            return raw_ddl["summary"]
        if not self.config.get("enable_llm_summary", True):
            return None
        summary = await summarize_ddl(raw_ddl, event, self.context)
        if not summary:
            return None

        raw_ddl["summary"] = summary
        # 回存到 KV（加锁保证并发安全）
        key = f"ddl_{group_id}"
        lock = self._get_summary_lock(group_id)
        async with lock:
            ddl_list = await self.get_kv_data(key, [])
            for ddl in ddl_list:
                if ddl.get("message_id") == raw_ddl.get("message_id"):
                    ddl["summary"] = summary
                    break
            await self.put_kv_data(key, ddl_list)
        return summary

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
        valid_ddls, removed_count = clean_expired_ddls(ddl_list, now)
        if removed_count > 0:
            await self.put_kv_data(key, valid_ddls)

        today_ddls = filter_today(valid_ddls)
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
            if not should_monitor_group(gid, silent_whitelist, group_list_str):
                continue

            key = f"ddl_{gid}"
            ddl_list = await self.get_kv_data(key, [])
            now = datetime.now()
            valid_ddls, removed_count = clean_expired_ddls(ddl_list, now)
            if removed_count > 0:
                await self.put_kv_data(key, valid_ddls)

            today_ddls = filter_today(valid_ddls)
            for ddl in today_ddls:
                ddl["group_id"] = gid
            all_today_ddls.extend(today_ddls)

        if not all_today_ddls:
            return "📭 所有监听群今日暂无 DDL 记录"

        # 归并所有群的 DDL，渲染单张卡片
        merged_id = "__admin_all_groups__"
        return await self._format_ddl_output(event, merged_id, all_today_ddls)

    async def _format_ddl_output(self, event, group_id, today_ddls):
        """格式化 DDL 输出，返回 (type, content)。summary 不回存（已由 on_group_message 缓存）"""
        urgent_hours = self.config.get("urgent_hours", 24)
        soon_hours = self.config.get("soon_hours", 48)
        urgent_ddls, soon_ddls, normal_ddls = categorize_ddls(today_ddls, urgent_hours, soon_hours)

        if self.config.get("enable_llm_summary", True):
            # 收集需要总结的 DDL → 批量 LLM → 按 group 回存
            to_summarize = [(idx, ddl) for idx, ddl
                            in enumerate(urgent_ddls + soon_ddls + normal_ddls)
                            if not ddl.get("summary")]
            # 按 group 分组（仅对 admin_all_groups 场景）
            by_group: dict[str, list] = {}
            for _, ddl in to_summarize:
                gid = ddl.get("group_id", "unknown")
                by_group.setdefault(gid, []).append(ddl)

            for gid, ddls in by_group.items():
                for ddl in ddls:
                    summary = await summarize_ddl(ddl, event, self.context)
                    if summary:
                        ddl['summary'] = summary
                # 回存到 KV
                if ddls:
                    key = f"ddl_{gid}"
                    lock = self._get_summary_lock(gid)
                    async with lock:
                        ddl_list = await self.get_kv_data(key, [])
                        for ddl_in_list in ddl_list:
                            mid = ddl_in_list.get("message_id")
                            for d in ddls:
                                if d.get("message_id") == mid:
                                    if d.get("summary"):
                                        ddl_in_list["summary"] = d["summary"]
                                    break
                        await self.put_kv_data(key, ddl_list)

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
                if ddl.get("summary"):
                    continue
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

        # force 模式直接跳过去重，不影响后台循环的 _reminded_ddls
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

    @filter.command("ddl_debug")
    async def debug_ddl(self, event: AstrMessageEvent) -> MessageEventResult:
        """调试模式：逐步追踪 DDL 检测全过程（仅管理员）"""
        if not self._is_admin(event):
            yield event.plain_result("❌ 仅管理员可用 /ddl_debug")
            return
        if not self.config.get("debug_mode", False):
            yield event.plain_result("⚠️ 调试模式未开启，请在设置中启用 debug_mode")
            return

        # 获取测试消息
        test_msg = event.message_str.strip()
        prefix = "/ddl_debug"
        if test_msg.startswith(prefix):
            test_msg = test_msg[len(prefix):].strip()
        if not test_msg:
            yield event.plain_result("用法: /ddl_debug <测试消息>")
            return

        lines = ["🔍 DDL 调试追踪", "─" * 30, f"📝 输入: {test_msg}", ""]

        # Step 1: 关键词 + 正则
        lines.append("【1】关键词 → 正则模式")
        lines.append(f"  关键词: {self.keywords}")
        lines.append(f"  正则: {self.ddl_pattern.pattern[:120]}...")
        lines.append("")

        # Step 2: 正则匹配
        ddl_info = extract_ddl(test_msg, self.ddl_pattern, resolve_relative_time)
        if not ddl_info:
            lines.append("【2】正则匹配 ❌ 未命中")
            lines.append("  检测结束：正则未匹配到 DDL 格式")
            yield event.plain_result("\n".join(lines))
            return

        task_desc, ddl_time = ddl_info
        lines.append("【2】正则匹配 ✅ 命中")
        lines.append(f"  匹配文本: {self.ddl_pattern.search(test_msg).group(0) if self.ddl_pattern.search(test_msg) else '?'}")
        lines.append(f"  提取 task: {task_desc[:60]}{'...' if len(task_desc) > 60 else ''}")
        lines.append(f"  提取 time: {ddl_time}")
        lines.append(f"  resolve_relative_time: {ddl_time} → {resolve_relative_time(ddl_time) if ddl_time else '?'}")
        lines.append("")

        # Step 3: LLM 语义验证（如果开启）
        detect_mode = self.config.get("ddl_detect_mode", "仅正则")
        lines.append(f"【3】检测模式: {detect_mode}")

        if detect_mode == "正则+LLM 验证":
            provider_id = self.config.get("ddl_llm_provider", "") or None
            lines.append("")
            lines.append("【3a】LLM 语义验证 - 发送 prompt:")
            prompt_text = CLASSIFY_PROMPT + test_msg
            # 截断过长 prompt
            if len(prompt_text) > 300:
                lines.append(f"  {prompt_text[:150]}")
                lines.append(f"  ...(省略 {len(prompt_text) - 300} 字)...")
                lines.append(f"  ...{prompt_text[-150:]}")
            else:
                for line in prompt_text.split("\n"):
                    lines.append(f"  {line}")
            lines.append("")

            try:
                if not provider_id:
                    umo = event.unified_msg_origin
                    provider_id = await self.context.get_current_chat_provider_id(umo=umo)
                if not provider_id:
                    lines.append("【3b】LLM 响应: ❌ 未找到可用的 LLM 模型")
                    lines.append("  → 检测结果: 放行（使用正则结果）")
                else:
                    lines.append(f"【3b】使用模型: {provider_id}")
                    llm_resp = await self.context.llm_generate(
                        chat_provider_id=provider_id,
                        prompt=CLASSIFY_PROMPT + test_msg,
                    )
                    if not llm_resp or not llm_resp.completion_text:
                        lines.append("【3c】LLM 响应: ❌ 空响应")
                        lines.append("  → 检测结果: 放行（使用正则结果）")
                    else:
                        raw = llm_resp.completion_text.strip()
                        lines.append(f"【3c】LLM 原始响应: {raw}")
                        # 解析
                        import json as _json
                        clean = raw
                        if clean.startswith("```"):
                            clean = clean.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                        try:
                            result = _json.loads(clean)
                            if result.get("is_ddl"):
                                lines.append(f"【3d】解析结果: ✅ 是 DDL")
                                lines.append(f"  LLM task: {result.get('task', '?')}")
                                lines.append(f"  LLM time: {result.get('ddl_time', '?')}")
                                # Apply LLM correction
                                if result.get("task"):
                                    task_desc = result["task"]
                                if result.get("ddl_time"):
                                    ddl_time = resolve_relative_time(result["ddl_time"])
                            else:
                                lines.append(f"【3d】解析结果: ❌ 不是 DDL（误报过滤）")
                                lines.append("  → 检测结束：LLM 判定为误报，不触发")
                                yield event.plain_result("\n".join(lines))
                                return
                        except _json.JSONDecodeError:
                            lines.append(f"【3d】解析结果: ⚠️ JSON 解析失败，放行")
            except Exception as e:
                lines.append(f"【3】LLM 调用异常: {e}")
                lines.append("  → 检测结果: 放行（使用正则结果）")
        else:
            lines.append("  → 仅正则模式，跳过 LLM 验证")
        lines.append("")

        # Step 4: 最终解析
        lines.append("【4】最终解析")
        parsed = parse_ddl_time(ddl_time)
        lines.append(f"  parse_ddl_time('{ddl_time}') → {parsed}")
        if parsed:
            import datetime as _dt
            remaining = (parsed - _dt.datetime.now()).total_seconds() / 3600
            lines.append(f"  剩余时间: {remaining:.1f} 小时")
        lines.append("")

        # Step 5: LLM 总结测试（实际调用并展示 prompt + 响应）
        lines.append("【5】LLM 总结测试")
        if self.config.get("enable_llm_summary", True):
            dummy_ddl = {"task": task_desc, "ddl_time": ddl_time}
            summary_prompt = f"用不超过50个字总结以下DDL任务，不要带任何标点符号，直接输出总结文本：\n\n任务：{dummy_ddl.get('task', '?')}\n截止：{dummy_ddl['ddl_time']}"
            lines.append(f"  发送 prompt:")
            for pline in summary_prompt.split("\n"):
                lines.append(f"    {pline}")
            lines.append("")
            try:
                import re as _re
                # 手动调 LLM 以获取原始响应
                umo = event.unified_msg_origin
                llm_pid = await self.context.get_current_chat_provider_id(umo=umo)
                if llm_pid:
                    raw_resp = await self.context.llm_generate(
                        chat_provider_id=llm_pid,
                        prompt=summary_prompt,
                    )
                    if raw_resp and raw_resp.completion_text:
                        raw_text = raw_resp.completion_text.strip()
                        lines.append(f"  LLM 原始响应: {raw_text}")
                        # 展示清洗过程
                        cleaned = _re.sub(r'[，。！？、；：""''（）【】《》\s]', '', raw_text)
                        cleaned = cleaned[:50] if len(cleaned) > 50 else cleaned
                        lines.append(f"  清洗后: {cleaned}")
                    else:
                        lines.append(f"  LLM 原始响应: (空)")
                else:
                    lines.append(f"  未找到 LLM 模型")
            except Exception as e:
                lines.append(f"  总结异常: {e}")
        else:
            lines.append("  ❌ LLM 总结: 已关闭")
        lines.append("")

        # Step 6: 会触发什么
        lines.append("【6】触发行为")
        if self.config.get("enable_auto_reply", False):
            lines.append("  ✅ auto_reply: 会在群内回复")
        else:
            lines.append("  ❌ auto_reply: 已关闭")
        if self.config.get("silent_mode", True) and self.admin_ids:
            lines.append(f"  ✅ silent_push: 会推送给 {len(self.admin_ids)} 位管理员")
        else:
            lines.append("  ❌ silent_push: 未启用或无管理员")
        if self.config.get("enable_llm_summary", True):
            lines.append("  ✅ LLM 总结: 会调用 LLM 生成摘要")
        else:
            lines.append("  ❌ LLM 总结: 已关闭")

        yield event.plain_result("\n".join(lines))

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
        """检查所有监听群的 DDL，提醒即将截止的。force=True 时忽略时间窗口和去重，仅取第一条。
        返回 (发送数, 检查数, 不在窗口数, 解析失败数)"""
        from astrbot.api.star import StarTools
        import astrbot.api.message_components as Comp
        from astrbot.api.event import MessageChain

        now = datetime.now()
        notified_count = 0
        total_checked = 0
        in_window = 0
        skipped_parse = 0
        sent_dedup_keys = []  # 本次发送的去重 key，批量持久化

        for gid in list(self.monitored_groups):
            silent_whitelist = self.config.get("silent_whitelist", False)
            group_list_str = self.config.get("silent_group_list", "")
            if not should_monitor_group(gid, silent_whitelist, group_list_str):
                continue

            key = f"ddl_{gid}"
            ddl_list = await self.get_kv_data(key, [])
            valid_ddls, _ = clean_expired_ddls(ddl_list, now)

            for ddl in valid_ddls:
                total_checked += 1
                ddl_time_str = ddl.get('ddl_time', '')

                if force:
                    # 强制模式：跳过时间窗口和去重，仅取第一条
                    if notified_count >= 1:
                        continue
                else:
                    deadline = parse_ddl_time(ddl_time_str)
                    if not deadline:
                        skipped_parse += 1
                        continue

                    remaining = (deadline - now).total_seconds() / 3600
                    if remaining < 0 or remaining > remind_hours:
                        continue
                    in_window += 1

                    # 去重
                    dedup_key = (gid, ddl.get('detected_at', ''), ddl_time_str)
                    if dedup_key in self._reminded_ddls:
                        continue
                    self._reminded_ddls.add(dedup_key)
                    sent_dedup_keys.append(dedup_key)
                    deadline = deadline  # already computed
                    remaining = remaining

                if force:
                    deadline = parse_ddl_time(ddl_time_str)
                    remaining = 0.0
                    if deadline:
                        remaining = (deadline - now).total_seconds() / 3600

                if not deadline and force:
                    skipped_parse += 1
                    continue

                remaining = max(0, remaining)

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
                    sessions = self._get_admin_sessions(admin_id)
                    if not sessions:
                        logger.warning(f"[DeadlineReminder] 无法解析 admin {admin_id} 的平台")
                        continue
                    try:
                        chain = MessageChain()
                        chain.chain.append(Comp.Plain(msg_text))
                        sent_ok = False
                        for session in sessions:
                            try:
                                await StarTools.send_message(session, chain)
                                sent_ok = True
                                break
                            except Exception:
                                continue
                        if sent_ok:
                            notified_count += 1
                        else:
                            logger.warning(f"[DeadlineReminder] 所有平台均无法向 {admin_id} 发送")
                    except Exception as e:
                        logger.warning(f"[DeadlineReminder] 发送失败: {e}")

        # 持久化去重集
        if sent_dedup_keys:
            await self._persist_reminded_ddls()

        if notified_count > 0:
            logger.info(f"[DeadlineReminder] 已推送 {notified_count} 条截止提醒")
        else:
            reason = f"解析失败 {skipped_parse}" if not force else ""
            logger.info(f"[DeadlineReminder] 检查 {total_checked} 条 DDL，窗口内 {in_window} 条，均无新提醒，{reason}")

        return (notified_count, total_checked, 0, skipped_parse)

    async def _persist_reminded_ddls(self):
        """将 _reminded_ddls 持久化到 KV（最多保留最近 500 条）"""
        data = list(self._reminded_ddls)[-500:]
        await self.put_kv_data("__reminded_ddls", data)

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
