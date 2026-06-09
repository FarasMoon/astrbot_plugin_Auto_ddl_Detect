"""时间解析模块：相对时间 + 格式解析"""

import re
from datetime import datetime, timedelta
from typing import Optional


def resolve_relative_time(matched_time: str) -> str:
    """解析相对时间，保留原始精度（日期/时间）"""
    now = datetime.now()

    if re.match(r'\d{1,2}月\d{1,2}[日号]?', matched_time) or re.match(r'\d{1,2}[/-]\d{1,2}', matched_time):
        return re.sub(r'[（(][^）)]*[）)]', '', matched_time).strip()

    if matched_time in ["今天", "明天", "今晚", "明晚"]:
        if matched_time in ["明天", "明晚"]:
            result = (now + timedelta(days=1)).strftime("%m月%d日")
        else:
            result = now.strftime("%m月%d日")
        time_match = re.search(r'[0-2]?\d[点时:：][0-5]?\d', matched_time)
        if time_match:
            result += matched_time[time_match.start():time_match.end()]
        return result

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
            result = target.strftime("%m月%d日")
            time_match = re.search(r'[早晚]?\s*[0-2]?\d[点时:：][0-5]?\d?分?', matched_time)
            if time_match:
                result += matched_time[time_match.start():time_match.end()].strip()
            elif '晚' in matched_time or '早' in matched_time:
                if '晚' in matched_time:
                    result += "晚上"
                elif '早' in matched_time:
                    result += "早上"
            return result

    return matched_time


def parse_ddl_time(time_str: str) -> Optional[datetime]:
    """解析 DDL 时间字符串为 datetime 对象，自动修正跨年"""
    if not time_str:
        return None

    now = datetime.now()
    year = now.year

    def _correct_year(dt: datetime) -> datetime:
        """如果解析结果已过去超过 180 天，尝试下一年；如果超过 180 天后，可能是下一年边缘"""
        diff_days = (dt - now).days
        if diff_days < -180:
            # 已过去很久 → 很可能是下一年
            return dt.replace(year=year + 1)
        return dt

    try:
        formats = [
            "%Y年%m月%d日 %H:%M",
            "%Y年%m月%d日%H:%M",
            "%m月%d日 %H:%M",
            "%m月%d日%H:%M",
            "%m月%d日 %H点%M分",
            "%m月%d日%H点%M分",
            "%m月%d日 %H点",
            "%m月%d日%H点",
            "%m-%d %H:%M",
            "%m月%d日",
            "%m-%d"
        ]

        for fmt in formats:
            try:
                dt = datetime.strptime(time_str, fmt)
                if dt.year == 1900:
                    dt = dt.replace(year=year)
                return _correct_year(dt)
            except ValueError:
                continue

        if "今晚" in time_str:
            match = re.search(r"(\d{1,2})[:：点时](\d{2})", time_str)
            if match:
                return datetime(year, now.month, now.day, int(match.group(1)), int(match.group(2)))
            return datetime(year, now.month, now.day, 23, 59)

        if "明天" in time_str:
            tomorrow = now + timedelta(days=1)
            match = re.search(r"(\d{1,2})[:：点时](\d{2})", time_str)
            if match:
                return datetime(year, tomorrow.month, tomorrow.day, int(match.group(1)), int(match.group(2)))
            return datetime(year, tomorrow.month, tomorrow.day, 23, 59)

        period_match = re.search(r"(早上|上午|中午|下午|晚上|夜里)\s*(\d{1,2})[:：点时]?(\d{2})?", time_str)
        if period_match:
            period = period_match.group(1)
            hour = int(period_match.group(2))
            minute = int(period_match.group(3)) if period_match.group(3) else 0
            if period in ["下午", "晚上", "夜里"] and hour < 12:
                hour += 12
            return datetime(year, now.month, now.day, hour, minute)

    except Exception:
        pass

    return None
