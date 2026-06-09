<div align="center">

<img src="logo.png" width="360" alt="AutoDDLDetect">

# Auto DDL Detect

AstrBot 群聊 DDL 自动检测插件

[![version](https://img.shields.io/badge/version-1.2.1-blue.svg)](https://github.com/FarasMoon/astrbot_plugin_Auto_ddl_Detect)
[![license](https://img.shields.io/badge/license-AGPL--3.0-green.svg)](LICENSE)
[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.5.7-orange.svg)](https://github.com/Soulter/AstrBot)
[![python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

</div>

> **作者**: FarasMoon | [仓库](https://github.com/FarasMoon/astrbot_plugin_Auto_ddl_Detect)
>
> **注意**: 本插件仅支持 OneBot 适配器
>
> [!ATTENTION]
> 大量 vibe coding 注意
> 可能维护困难

## 功能


| 关 | 方式             | 说明                      |
| -- | ---------------- | ------------------------- |
| 1  | 关键词预筛       | 不含 DDL 关键词则直接丢弃 |
| 2  | 正则匹配         | 关键词 + 时间格式组合     |
| 3  | LLM 验证（可选） | 语义判断，过滤闲聊误报    |

可在设置切换"正则+LLM 验证"模式。

### LLM 总结

DDL 首次检测时调用大模型生成精简摘要（不超过 50 字）。

### 自定义时间正则

内置 7 条时间模式覆盖常见中文表达。可在设置中添加自定义正则（每行一条），如 `\d{4}-\d{2}-\d{2}`。

### 紧急分类


| 分类     | 默认阈值    |
| -------- | ----------- |
| 马上截止 | 24 小时内   |
| 很快截止 | 48 小时内   |
| 普通     | 48 小时以上 |

### 静默监听 + 截止前提醒

跨平台监听群聊 DDL，汇总推送给管理员私聊。截止前定时扫描，汇总为一张表一次性调用 LLM 生成提醒。

## 安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/FarasMoon/astrbot_plugin_Auto_ddl_Detect.git
```

重启 AstrBot 即可。无额外依赖。

## 配置


| 配置项                    | 类型       | 说明                       | 默认值                                       |
| ------------------------- | ---------- | -------------------------- | -------------------------------------------- |
| `ddl_keywords`            | 文本       | DDL 关键词（逗号分隔）     | `截止,截止时间,截止日期,deadline,ddl,交作业` |
| `ddl_detect_mode`         | 选择       | 检测模式                   | `仅正则`                                     |
| `ddl_llm_provider`        | LLM 选择器 | DDL 验证专用模型           | 空                                           |
| `custom_time_patterns`    | 文本       | 自定义时间正则（每行一条） | 空                                           |
| `enable_llm_summary`      | 开关       | LLM 总结                   | 开                                           |
| `enable_auto_reply`       | 开关       | 检测后自动群内回复         | 关                                           |
| `output_as_image`         | 开关       | 图片模式输出               | 开                                           |
| `silent_mode`             | 开关       | 静默监听                   | 开                                           |
| `silent_admin_sid`        | 文本       | 管理员 ID（逗号分隔）      | 空                                           |
| `deadline_remind_enabled` | 开关       | 截止前提醒                 | 开                                           |
| `deadline_remind_hours`   | 浮点       | 提前提醒小时数（-1 禁用）  | 6                                            |
| `debug_mode`              | 开关       | 调试模式                   | 关                                           |

## 指令


| 指令                | 说明               | 范围                        |
| ------------------- | ------------------ | --------------------------- |
| `/ddl`              | 查询今日 DDL       | 群聊 / 管理员私聊（汇总）   |
| `/clearddl`        | 清除本群今日 DDL   | 群聊；管理员私聊清除全群    |
| `/清除所有ddl`      | 清除所有群 DDL     | 仅管理员                    |
| `/ddl_remind_test`  | 触发截止前提醒测试 | 任何人                      |
| `/ddl_debug <消息>` | 逐步追踪检测全过程 | 管理员（需开启 debug_mode） |

## 许可证

[AGPL-3.0](LICENSE)

[提交 Issue](https://github.com/FarasMoon/astrbot_plugin_Auto_ddl_Detect/issues)
