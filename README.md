<div align="center">

<img src="logo.png" width="360" alt="AutoDDLDetect">

# AutoDDLDetect

AstrBot 群聊 DDL 自动检测插件

[![version](https://img.shields.io/badge/version-1.2.0-blue.svg)](https://github.com/FarasMoon/astrbot_plugin_Auto_ddl_Detect)
[![license](https://img.shields.io/badge/license-AGPL--3.0-green.svg)](LICENSE)
[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.5.7-orange.svg)](https://github.com/Soulter/AstrBot)
[![python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

</div>

> **作者**: FarasMoon | [仓库](https://github.com/FarasMoon/astrbot_plugin_Auto_ddl_Detect)
>
> [!ATTENTION]
> 大量 vibe coding 注意
> 可能维护困难

---

## 功能

### 自动检测

正则匹配关键词 + 时间格式，自动识别群聊中的 DDL 消息。

### LLM 总结

调用大模型对 DDL 内容进行不超过 50 字的精简总结，图片和文字模式均支持。

### 紧急分类

可配置时间阈值，分三类展示：

| 分类 | 默认阈值 |
| --- | --- |
| 马上截止 | 24 小时内 |
| 很快截止 | 48 小时内 |
| 普通 | 48 小时以上 |

### 图片 / 文字双模式

- **图片模式**：HTML 卡片渲染，支持随机背景图或纯色背景
- **文字模式**：文本格式输出
- 通过命令即时切换，每个群独立记忆

### 定时通知

支持多时间点（如 `08:00, 18:00, 22:00`），每日自动推送 DDL 列表。

### 静默监听

跨平台监听群聊 DDL，汇总推送给管理员私聊：

- 跨平台认证（`sender.user_id`）
- 多管理员（逗号分隔）
- 黑名单 / 白名单群过滤（开关切换）
- 管理员私聊 `/ddl` 查看所有监听群汇总卡片
- 汇总卡片显示来源（群名 + 群号）

### 截止前提醒

定时扫描即将截止的 DDL，提前推送提醒：

- 可配置提前时间（默认 6 小时，-1 禁用）
- 使用 AstrBot 人格选择器，由 LLM 按角色语气生成提醒
- 自动适配所有活跃平台发送（无需手动填平台名）
- 同一条 DDL 仅提醒一次

### 过期清理

超过截止时间的 DDL 自动从缓存中移除。

---

## 安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/FarasMoon/astrbot_plugin_Auto_ddl_Detect.git
```

重启 AstrBot 即可加载。无额外依赖。

---

## 配置

### 基础配置

| 配置项 | 类型 | 说明 | 默认值 |
| --- | --- | --- | --- |
| `ddl_keywords` | 文本 | DDL 检测关键词 | `截止,截止时间,截止日期,deadline,ddl,交作业` |
| `enable_llm_summary` | 开关 | 启用 LLM 总结 | 开 |
| `enable_auto_reply` | 开关 | 检测到 DDL 后自动回复 | 关 |
| `urgent_hours` | 数字 | "马上截止" 阈值（小时） | 24 |
| `soon_hours` | 数字 | "很快截止" 阈值（小时） | 48 |
| `output_as_image` | 开关 | 以图片形式输出 | 开 |
| `background_as_image` | 开关 | 使用背景图（否则纯色） | 开 |
| `background_api` | 文本 | 背景图 API | `https://t.alcy.cc/moez` |
| `background_color` | 文本 | 纯色背景 | `#f0f0f0` |
| `background_opacity` | 浮点 | 背景图透明度 | 0.12 |

### 监听与提醒配置

| 配置项 | 类型 | 说明 | 默认值 |
| --- | --- | --- | --- |
| `silent_mode` | 开关 | 静默监听模式 | 开 |
| `silent_whitelist` | 开关 | 白名单模式（关=黑名单） | 关 |
| `silent_group_list` | 文本 | 群过滤列表（逗号分隔） | 空 |
| `silent_admin_sid` | 文本 | 管理员用户 ID（逗号分隔） | 空 |
| `group_display` | 文本 | 群名称 JSON 映射 | 空 |
| `enable_notification` | 开关 | 定时通知 | 关 |
| `notification_times` | 文本 | 通知时间（HH:MM,HH:MM） | `08:00` |
| `deadline_remind_enabled` | 开关 | 截止前提醒 | 开 |
| `deadline_remind_hours` | 浮点 | 提前提醒小时数（-1 禁用） | 6 |
| `deadline_remind_persona` | 人格选择器 | 提醒时使用的人格 | 空 |

---

## 指令

| 指令 | 说明 | 可用范围 |
| --- | --- | --- |
| `/ddl` | 查询今日 DDL | 群聊 / 管理员私聊（汇总） |
| `/clearddl` `/清除ddl` | 清除当前群今日 DDL | 群聊 / 管理员私聊（全清） |
| `/清除所有ddl` | 清除所有群的 DDL | 仅管理员 |
| `/ddl_image` | 切换到图片输出 | 群聊 |
| `/ddl_text` | 切换到文字输出 | 群聊 |
| `/ddl_test` | 测试通知效果 | 群聊 |
| `/ddl_remind_test` | 强制触发一条截止提醒 | 任何消息 |
| `/ddl_personas` | 查看可用人格列表 | 任何消息 |

---

## 群名映射

在 `group_display` 中配置 JSON 格式的群号→群名映射，管理员汇总卡片将显示对应群名：

```json
{"721647196": "学术群", "798012": "项目组"}
```

---

## 许可证

[AGPL-3.0](LICENSE)

---

[提交 Issue](https://github.com/FarasMoon/astrbot_plugin_Auto_ddl_Detect/issues)
