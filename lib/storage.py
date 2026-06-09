"""DDL 数据管理：清理过期 + 过滤 + 常量"""

from datetime import datetime

from .time_parser import parse_ddl_time

# 每群最多保留的 DDL 条数
MAX_DDL_PER_GROUP = 500


def clean_expired_ddls(ddl_list: list, now: datetime) -> tuple:
    """清理已过期的 DDL（使用统一的 parse_ddl_time），返回 (有效列表, 清理数量)
    无法解析时间的 DDL：如果已检测超过 30 天则视为过期清理"""
    valid_ddls = []
    removed_count = 0
    for ddl in ddl_list:
        ddl_time_str = ddl.get('ddl_time', '')
        try:
            deadline = parse_ddl_time(ddl_time_str)
            if deadline:
                if deadline < now:
                    removed_count += 1
                    continue
                valid_ddls.append(ddl)
                continue
        except Exception:
            pass
        # 无法解析时间 → 靠检测时间兜底（30天过期）
        detected_str = ddl.get('detected_at', '')
        if detected_str:
            try:
                detected = datetime.strptime(detected_str[:10], "%Y-%m-%d")
                if (now - detected).days > 30:
                    removed_count += 1
                    continue
            except ValueError:
                pass
        valid_ddls.append(ddl)
    return valid_ddls, removed_count


def filter_today(ddls: list) -> list:
    """筛选今天的 DDL"""
    today = datetime.now().strftime("%Y-%m-%d")
    return [ddl for ddl in ddls if ddl.get('detected_at', '').startswith(today)]
