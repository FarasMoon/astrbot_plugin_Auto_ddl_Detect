"""LLM 总结模块"""

import asyncio
import re
from typing import Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

MAX_SUMMARY_LEN = 50
LLM_TIMEOUT = 15  # LLM 调用超时秒数


async def summarize_ddl(ddl_data: dict, event: AstrMessageEvent, context) -> Optional[str]:
    """调用 LLM 总结 DDL（不超过50字）"""
    try:
        umo = event.unified_msg_origin
        llm_provider = await context.get_current_chat_provider_id(umo=umo)

        if not llm_provider:
            logger.warning("LLM 总结：未找到可用的聊天模型")
            return None

        prompt = f"""用不超过50个字总结以下DDL任务，不要带任何标点符号，直接输出总结文本：

任务：{ddl_data.get('task', '未知')}
截止：{ddl_data['ddl_time']}"""

        llm_resp = await asyncio.wait_for(
            context.llm_generate(
                chat_provider_id=llm_provider,
                prompt=prompt,
            ),
            timeout=LLM_TIMEOUT
        )
        if not llm_resp or not llm_resp.completion_text:
            logger.warning("LLM 总结：模型返回为空")
            return None

        result = llm_resp.completion_text.strip()
        logger.info(f"LLM 总结原始返回: {result}")
        result = re.sub(r'[，。！？、；：""''（）【】《》\s]', '', result)
        return result[:MAX_SUMMARY_LEN] if len(result) > MAX_SUMMARY_LEN else result
    except Exception as e:
        logger.error(f"LLM 总结失败: {e}")
        return None
