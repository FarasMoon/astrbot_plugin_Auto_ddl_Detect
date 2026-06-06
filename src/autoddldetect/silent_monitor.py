"""静默监听模块 - 跨群监听 DDL 并推送给管理员"""

from datetime import datetime


def should_monitor_group(
    group_id: str,
    group_mode: str,
    group_list_str: str,
) -> bool:
    """判断群是否应被静默监听

    Args:
        group_id: 群号
        group_mode: blacklist / whitelist
        group_list_str: 逗号分隔的群号列表

    Returns:
        True 表示该群需要被监听
    """
    if not group_list_str.strip():
        return group_mode == "blacklist"

    group_ids = [g.strip() for g in group_list_str.split(",") if g.strip()]
    if group_mode == "blacklist":
        return group_id not in group_ids
    else:
        return group_id in group_ids


def format_silent_msg(raw_ddl: dict) -> str:
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
