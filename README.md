# Persona+

![:name](https://count.getloli.com/@astrbot_plugin_persona_plus?name=astrbot_plugin_persona_plus&theme=miku&padding=7&offset=0&align=top&scale=1&pixelated=1&darkmode=auto)

Persona+ 是一个 AstrBot 人格管理增强插件，用于更方便地创建、查看、更新、删除和切换人格。

> [!tip]
> 1.3.2 版本对配置文件进行了较大改动。升级后如果遇到配置无法保存的情况，请清除本插件配置文件并重载插件。

## 功能概览

| 能力 | 说明 |
| --- | --- |
| 人格管理 | 支持创建、更新、删除、查看人格 |
| 快捷切换 | 支持 `pp <人格ID>` 快速切换当前会话人格 |
| 文件夹路径 | 支持使用 `文件夹/人格ID` 定位人格 |
| 关键词切换 | 根据消息关键词自动切换到指定人格 |
| 上下文控制 | 切换人格后可自动清空当前对话上下文 |
| QQ 资料同步 | 切换人格时可同步 QQ 昵称、群名片和头像（仅适配 NapCat） |
| 函数工具 | 可向 LLM 暴露人格管理工具，支持完整创建/更新人格 |

## 使用方式

Persona+ 提供两类入口：

| 入口 | 适用场景 | 能力范围 |
| --- | --- | --- |
| 指令 | 日常手动管理，追求简单直接 | 创建/更新 System Prompt、查看、删除、头像、切换 |
| 函数工具 | 让 LLM 通过自然语言管理人格 | 支持预设对话、函数工具/MCP 工具、Skills、自定义错误回复等完整字段 |

指令入口刻意保持简单，不暴露工具和 Skills 配置；如需调整人格可用工具、MCP 工具或 Skills，请使用 WebUI，或启用函数工具后通过自然语言让 LLM 处理。

## 指令

命令组：`persona_plus`

别名：`pp`、`persona+`

| 指令 | 说明 |
| --- | --- |
| `pp <人格ID>` | 快速切换当前会话人格 |
| `pp <文件夹/人格ID>` | 使用文件夹路径快速切换人格 |
| `pp help` | 显示帮助与命令说明 |
| `pp list [文件夹路径]` | 列出全部人格，或列出指定文件夹下的人格树 |
| `pp view <文件夹/人格ID>` | 查看指定人格详情 |
| `pp create <文件夹/人格ID>` | 创建新人格，随后发送文本或 `.txt` / `.md` 文件作为 System Prompt |
| `pp update <文件夹/人格ID>` | 更新现有人格，随后发送文本或 `.txt` / `.md` 文件作为新的 System Prompt |
| `pp avatar <文件夹/人格ID>` | 上传或更新人格头像，随后发送图片 |
| `pp delete <文件夹/人格ID>` | 删除指定人格 |

### 指令示例

| 示例 | 说明 |
| --- | --- |
| `pp list` | 查看全部人格 |
| `pp list 测试人格` | 查看 `测试人格` 文件夹下的人格 |
| `pp 女仆` | 切换到 `女仆` 人格 |
| `pp view 测试人格/女仆` | 查看 `测试人格/女仆` 的详情 |
| `pp create 测试人格/新角色` | 在 `测试人格` 文件夹中创建 `新角色` |
| `pp update 1` | 使用最近一次 `list` 输出中的序号更新人格 |

### 路径与序号

- 人格路径使用 `/` 分隔，例如 `测试人格/女仆`。
- 大多数情况下可以只写人格 ID，不必带文件夹路径。
- `list` 输出的人格序号可用于 `view`、`update`、`avatar`、`delete` 和快捷切换。
- `create` 指令中的文件夹不存在时会自动创建。

## 函数工具

函数工具由配置项 `llm_tool_options` 控制，默认不暴露。建议按最小权限原则，只启用实际需要的工具。

| 配置值 | 暴露工具 | 能力 |
| --- | --- | --- |
| `list` | `persona_plus_list` | 查询人格列表 |
| `switch` | `persona_plus_switch` | 切换当前会话人格 |
| `view` | `persona_plus_view` | 查看人格详情 |
| `create` | `persona_plus_create` | 创建人格，支持完整字段 |
| `update` | `persona_plus_update` | 更新人格，支持按字段修改 |
| `delete` | `persona_plus_delete` | 删除人格 |

### 完整创建/更新字段

`persona_plus_create` 与 `persona_plus_update` 支持以下字段：

| 字段 | 创建 | 更新 | 说明 |
| --- | --- | --- | --- |
| `persona_reference` | 必填 | 必填 | 人格 ID，或 `文件夹/人格ID` 路径 |
| `system_prompt` | 必填 | 可选 | 人格 System Prompt 文本；更新时省略表示不修改 |
| `begin_dialogs` | 可选 | 可选 | 预设对话数组，需按“用户、助手、用户、助手”的顺序填写，数量必须为偶数；更新时传 `[]` 可清空，省略则不修改 |
| `tools` | 可选 | 可选 | 人格可用函数工具名列表，MCP 工具同样填写工具名 |
| `skills` | 可选 | 可选 | 人格可用 Skills 名称列表 |
| `custom_error_message` | 可选 | 可选 | 此人格请求失败时发送给用户的自定义错误回复；更新时传空字符串可清空，省略则不修改 |

### `tools` / `skills` 取值语义

| 取值 | 创建时含义 | 更新时含义 |
| --- | --- | --- |
| `null` | 使用全部工具 / Skills | 改为使用全部工具 / Skills |
| `[]` | 禁用全部工具 / Skills | 改为禁用全部工具 / Skills |
| `["a", "b"]` | 仅启用指定工具 / Skills | 改为仅启用指定工具 / Skills |
| 省略字段 | 等同 `null` | 保持原配置不变 |

### 自然语言示例

- “创建一个叫 `绘图/海报助手` 的人格，提示词是……，只允许使用 `generate_image` 和 `web_search` 工具。”
- “把 `客服助手` 的 Skills 改成只使用 `faq_search`，并把错误回复改为：当前服务繁忙，请稍后再试。”
- “更新 `测试人格`，禁用所有函数工具，但保留全部 Skills。”

## 关键词自动切换

开启 `enable_keyword_switching` 后，插件会根据 `keyword_mappings` 自动切换人格。

| 配置示例 | 说明 |
| --- | --- |
| `女仆:女仆` | 消息包含“女仆”时切换到 `女仆` 人格 |
| `画图:绘图/海报助手` | 消息包含“画图”时切换到 `绘图/海报助手` 人格 |

如果配置了自动切换提示模板，模板中可使用 `{persona_id}` 占位符。

## QQ 昵称与头像同步

QQ 资料同步仅适配 NapCat / OneBot 链路。

| 配置项 | 说明 |
| --- | --- |
| `sync_nickname_on_switch` | 切换人格时同步昵称或群名片 |
| `nickname_sync_mode` | 控制昵称同步模式 |
| `sync_avatar_on_switch` | 切换人格时同步头像 |
| `nickname_template` | 昵称/群名片模板，支持 `{persona_id}` |

### 昵称同步模式

| 模式 | 说明 |
| --- | --- |
| `profile` | 修改 QQ 昵称，群聊和私聊都会修改 QQ 昵称 |
| `group_card` | 群聊中只修改群名片，私聊不修改 |
| `hybrid` | 群聊中修改群名片，私聊中修改 QQ 昵称 |

## 配置项

| 配置项 | 类型/可选值 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `enable_keyword_switching` | bool | `true` | 是否启用关键词自动切换；关闭后仍可使用指令切换 |
| `keyword_mappings` | list | `["关键词:人格"]` | 关键词与人格映射列表，每项格式为 `关键词:人格ID`；人格 ID 可填写文件夹路径 |
| `auto_switch_scope` | `conversation` / `session` / `global` | `conversation` | 人格切换生效范围 |
| `manage_wait_timeout_seconds` | int | `60` | 创建、更新人格或上传头像时等待用户发送下一条内容的最长时间（秒） |
| `admin_commands` | list | `["create", "update", "delete", "avatar"]` | 需要管理员权限的指令列表；默认普通用户可用 `switch`、`view`、`list`、`help` |
| `llm_tool_options` | list | `[]` | 向 LLM 暴露的人格函数工具列表 |
| `enable_auto_switch_announce` | bool | `true` | 切换人格时是否发送提示 |
| `clear_context_on_switch` | bool | `false` | 切换人格后是否自动清空当前对话上下文 |
| `sync_nickname_on_switch` | bool | `true` | 切换人格时是否同步修改 QQ 昵称或群名片 |
| `nickname_sync_mode` | `profile` / `group_card` / `hybrid` | `group_card` | 昵称同步模式 |
| `sync_avatar_on_switch` | bool | `false` | 切换人格时是否同步修改 QQ 头像 |
| `nickname_template` | string | `[B0T]{persona_id}` | 昵称/群名片模板，支持 `{persona_id}` 占位符 |

