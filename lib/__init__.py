"""AutoDDLDetect Plugin for AstrBot"""

from .template import HTML_TMPL
from .detector import DEFAULT_KEYWORDS, parse_keywords, build_pattern, extract_ddl, classify_ddl, CLASSIFY_PROMPT
from .time_parser import resolve_relative_time, parse_ddl_time
from .summarizer import summarize_ddl
from .renderer import categorize_ddls, format_text_ddl, render_image_card
from .monitor import should_monitor_group, format_silent_msg
from .storage import MAX_DDL_PER_GROUP, clean_expired_ddls, filter_today

__all__ = [
    "HTML_TMPL",
    "DEFAULT_KEYWORDS",
    "parse_keywords",
    "build_pattern",
    "extract_ddl",
    "classify_ddl",
    "CLASSIFY_PROMPT",
    "resolve_relative_time",
    "parse_ddl_time",
    "summarize_ddl",
    "categorize_ddls",
    "format_text_ddl",
    "render_image_card",
    "should_monitor_group",
    "format_silent_msg",
    "MAX_DDL_PER_GROUP",
    "clean_expired_ddls",
    "filter_today",
]
