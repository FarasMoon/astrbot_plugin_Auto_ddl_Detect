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

---

## 功能

### 对消息进行筛选


| 关 | 方式             | 说明                                              |
| -- | ---------------- | ------------------------------------------------- |
| 1  | 关键词预筛       | 消息不含 DDL 关键词（`截止`、`ddl` 等）则直接丢弃 |
| 2  | 正则匹配         | 关键词 + 时间格式组合（如`ddl 6月10日`）          |
| 3  | LLM 验证（可选） | 语义判断是否为真实 DDL，过滤闲聊误报              |

可以在设置里切换"正则+LLM 验证"：正则命中后由 LLM 判断消息是否为真实 DDL，可过滤掉：

### 自定义时间正则

内置 7 条时间模式覆盖常见中文表达，支持在设置中添加自定义正则（每行一条），如 `\d{4}-\d{2}-\d{2}` 匹配 ISO 日期。

### LLM 总结

（可关闭）DDL 首次检测时调用大模型生成不超过 50 字的精简摘要，图片和文字模式均展示。

### 紧急分类

可配置时间阈值，分三类展示：


| 分类     | 默认阈值    |
| -------- | ----------- |
| 马上截止 | 24 小时内   |
| 很快截止 | 48 小时内   |
| 普通     | 48 小时以上 |

### 静默监听

监听群聊 DDL，汇总推送给特定用户（管理员）私聊：

- 多管理员（逗号分隔）
- 黑名单 / 白名单群过滤
- 管理员私聊 `/ddl` 查看所有监听群汇总卡片

### 截止前提醒

定时扫描即将截止的 DDL，**所有窗口内 DDL 汇总为一张表一次性发给 LLM**，生成一条简洁汇总提醒推送给管理员。避免逐条调用 LLM 浪费 Token。

- 可配置提前时间（默认 6 小时，-1 禁用）
- 支持 AstrBot 人格选择器，按角色语气生成提醒
- 同一条 DDL 仅提醒一次

### 过期清理

超过截止时间的 DDL 自动从缓存中移除。无法解析时间的 DDL 在 30 天后兜底清理。

---

## 安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/FarasMoon/astrbot_plugin_Auto_ddl_Detect.git
```

## 配置

### 检测配置


| 配置项                 | 类型       | 说明                                    | 默认值                                       |
| ---------------------- | ---------- | --------------------------------------- | -------------------------------------------- |
| `ddl_keywords`         | 文本       | DDL 关键词（逗号分隔）                  | `截止,截止时间,截止日期,deadline,ddl,交作业` |
| `ddl_detect_mode`      | 选择       | 检测模式                                | `仅正则`                                     |
| `ddl_llm_provider`     | LLM 选择器 | DDL 验证专用模型（正则+LLM 模式时生效） | 空                                           |
| `custom_time_patterns` | 文本       | 自定义时间正则（每行一条）              | 空                                           |
| `enable_llm_summary`   | 开关       | LLM 总结                                | 开                                           |
| `enable_auto_reply`    | 开关       | 检测到 DDL 后自动在群内回复             | 关                                           |

### 显示配置


| 配置项                | 类型 | 说明                    | 默认值                   |
| --------------------- | ---- | ----------------------- | ------------------------ |
| `urgent_hours`        | 数字 | "马上截止" 阈值（小时） | 24                       |
| `soon_hours`          | 数字 | "很快截止" 阈值（小时） | 48                       |
| `output_as_image`     | 开关 | 图片模式输出            | 开                       |
| `background_as_image` | 开关 | 使用背景图（否则纯色）  | 开                       |
| `background_api`      | 文本 | 背景图 API 地址         | `https://t.alcy.cc/moez` |
| `background_color`    | 文本 | 纯色背景（CSS 颜色值）  | `#f0f0f0`                |
| `background_opacity`  | 浮点 | 背景图透明度（0~1）     | 0.12                     |

### 监听与提醒配置


| 配置项                    | 类型       | 说明                               | 默认值 |
| ------------------------- | ---------- | ---------------------------------- | ------ |
| `silent_mode`             | 开关       | 静默监听模式                       | 开     |
| `silent_whitelist`        | 开关       | 白名单模式（关=黑名单）            | 关     |
| `silent_group_list`       | 文本       | 群过滤列表（逗号分隔的群号）       | 空     |
| `silent_admin_sid`        | 文本       | 管理员用户 ID（逗号分隔）          | 空     |
| `group_display`           | 文本       | 群名称 JSON 映射                   | 空     |
| `deadline_remind_enabled` | 开关       | 截止前提醒                         | 开     |
| `deadline_remind_hours`   | 浮点       | 提前提醒小时数（-1 禁用）          | 6      |
| `deadline_remind_persona` | 人格选择器 | 提醒时使用的 LLM 人格              | 空     |
| `debug_mode`              | 开关       | 调试模式（开启后可用`/ddl_debug`） | 关     |

---

## 指令


| 指令                   | 说明                   | 可用范围                            |
| ---------------------- | ---------------------- | ----------------------------------- |
| `/ddl`                 | 查询今日 DDL           | 群聊 / 管理员私聊（汇总所有监听群） |
| `/clearddl` `/清除ddl` | 清除本群今日 DDL       | 群聊 / 管理员私聊（清除所有群）     |
| `/清除所有ddl`         | 清除所有监听群的 DDL   | 仅管理员                            |
| `/ddl_remind_test`     | 强制触发截止前提醒测试 | 任何人                              |
| `/ddl_debug <消息>`    | 逐步追踪检测全过程     | 仅管理员（需开启 debug_mode）       |

---

## 群名映射

在 `group_display` 中配置 JSON 格式的群号→群名映射，管理员汇总卡片将显示对应群名：

```json
{"721647196": "学术群", "798012": "项目组"}
```

---

## 自定义时间正则

在设置页面的 `custom_time_patterns` 中按行添加 Python 正则，内置规则已覆盖：

- `6月10日`、`06-10`、`2025年6月10日`
- `今天`、`明天`、`今晚`、`明晚`
- `周三`、`下周一`、`下周天`

示例自定义规则：

```
\d{4}-\d{2}-\d{2}
\d{4}/\d{2}/\d{2}
```

---

## 许可证

[AGPL-3.0](LICENSE)

---

[提交 Issue](https://github.com/FarasMoon/astrbot_plugin_Auto_ddl_Detect/issues)
