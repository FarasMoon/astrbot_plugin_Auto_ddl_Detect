"""渲染模块：分类 + 格式化"""

import asyncio
from datetime import datetime
from typing import Any

from .time_parser import parse_ddl_time


def categorize_ddls(ddls: list, urgent_hours: int = 24, soon_hours: int = 48) -> tuple:
    """将 DDL 按时间紧急程度分类"""
    urgent_ddls = []
    soon_ddls = []
    normal_ddls = []

    now = datetime.now()

    for ddl in ddls:
        ddl_time = ddl.get('ddl_time', '')
        deadline = parse_ddl_time(ddl_time)

        if deadline:
            diff_hours = (deadline - now).total_seconds() / 3600
            if diff_hours <= urgent_hours:
                urgent_ddls.append(ddl)
            elif diff_hours <= soon_hours:
                soon_ddls.append(ddl)
            else:
                normal_ddls.append(ddl)
        else:
            normal_ddls.append(ddl)

    return urgent_ddls, soon_ddls, normal_ddls


def format_text_ddl(urgent_ddls: list, soon_ddls: list, normal_ddls: list,
                    urgent_hours: int = 24, soon_hours: int = 48,
                    source_info: str = "") -> str:
    """格式化文字 DDL 输出，分类显示（优先使用 LLM 总结）"""
    def _fmt(ddl):
        return ddl.get('summary') or ddl.get('task', '')[:20] or ddl.get('raw_message', '')[:20]

    def _group_label(ddl):
        gid = ddl.get('group_id', '')
        return f"[群{gid}] " if gid else ""

    result = []
    if source_info:
        result.append(f"📊 {source_info}")
        result.append("")

    result.append(f"🔥 马上截止 ({urgent_hours}小时内)：")
    if urgent_ddls:
        for i, ddl in enumerate(urgent_ddls, 1):
            sender = ddl.get('sender', '未知')
            ddl_time = ddl.get('ddl_time', '未知')
            result.append(f"  {i}. {_group_label(ddl)}{sender} | {_fmt(ddl)} | {ddl_time}")
    else:
        result.append("  空")

    result.append(f"⏰ 很快截止 ({soon_hours}小时内)：")
    if soon_ddls:
        for i, ddl in enumerate(soon_ddls, 1):
            sender = ddl.get('sender', '未知')
            ddl_time = ddl.get('ddl_time', '未知')
            result.append(f"  {i}. {_group_label(ddl)}{sender} | {_fmt(ddl)} | {ddl_time}")
    else:
        result.append("  空")

    result.append("📌 普通：")
    if normal_ddls:
        for i, ddl in enumerate(normal_ddls, 1):
            sender = ddl.get('sender', '未知')
            ddl_time = ddl.get('ddl_time', '未知')
            result.append(f"  {i}. {_group_label(ddl)}{sender} | {_fmt(ddl)} | {ddl_time}")
    else:
        result.append("  空")

    return "\n".join(result)


async def render_image_card(context: Any, urgent_ddls: list, soon_ddls: list,
                            normal_ddls: list, urgent_hours: int, soon_hours: int,
                            background_mode: str = "image",
                            background_value: str = "",
                            source_info: str = "",
                            gen_time: str = "") -> str:
    """渲染 DDL 图片"""
    from .template import HTML_TMPL

    date_str = datetime.now().strftime("%Y年%m月%d日 %A")

    template_vars = {
        "date": date_str,
        "urgent_ddls": urgent_ddls,
        "soon_ddls": soon_ddls,
        "normal_ddls": normal_ddls,
        "urgent_hours": str(urgent_hours),
        "soon_hours": str(soon_hours),
        "background_mode": background_mode,
        "background_color": background_value if background_mode == "color" else "",
        "background_url": background_value if background_mode == "image" else "",
        "background_opacity": "0.12",
        "source_info": source_info,
        "gen_time": gen_time,
    }

    return await asyncio.wait_for(
        context.html_render(HTML_TMPL, template_vars,
            options={"type": "png", "viewport": {"width": 520, "height": 100}, "full_page": True}),
        timeout=20
    )
