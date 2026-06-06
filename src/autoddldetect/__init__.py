"""AutoDDLDetect Plugin for AstrBot"""

from .template import HTML_TMPL
from .detector import DEFAULT_KEYWORDS, parse_keywords, build_pattern, extract_ddl
from .time_parser import resolve_relative_time, parse_ddl_time
from .summarizer import summarize_ddl
from .renderer import categorize_ddls, format_text_ddl, render_image_card
from .silent_monitor import should_monitor_group, format_silent_msg

__all__ = [
    "HTML_TMPL",
    "DEFAULT_KEYWORDS",
    "parse_keywords",
    "build_pattern",
    "extract_ddl",
    "resolve_relative_time",
    "parse_ddl_time",
    "summarize_ddl",
    "categorize_ddls",
    "format_text_ddl",
    "render_image_card",
    "should_monitor_group",
    "format_silent_msg",
]
