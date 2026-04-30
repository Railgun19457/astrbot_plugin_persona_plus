# Persona+ 

![:name](https://count.getloli.com/@astrbot_plugin_persona_plus?name=astrbot_plugin_persona_plus&theme=miku&padding=7&offset=0&align=top&scale=1&pixelated=1&darkmode=auto)

## 更好的人格设定管理插件

> [!tip]
> 1.3.2版本对配置文件进行了较大的改动，升级后如果遇到配置无法保存的情况，清除本插件的配置文件并重载插件即可

***
### 主要特性
- 使用指令或自然语言管理人格 (创建/更新/删除/查看)
- 支持基于关键词的自动切换
- 支持为人格上传头像，并在切换人格时同步切换QQ昵称和头像
- 切换人格时可选择自动清空当前会话上下文

### 指令
(命令组：`persona_plus`，别名：`pp`、`persona+`，在使用指令时，三者是等效的)

- 快捷切换：`pp <persona_id>` 或 `pp <文件夹/人格ID>`
  - 切换当前会话的人格
  
- `pp help`
  - 显示帮助与命令说明

- `pp list [文件夹路径]`
  - 列出所有已注册的人格，或指定文件夹下的人格树

- `pp view <文件夹/人格ID>`
  - 查看指定人格的 System Prompt、预设对话与工具配置

- `persona_plus create <文件夹/人格ID>`
  - 创建新人格。发送此命令后，请直接在聊天中发送要作为 System Prompt 的文本，或者文本文件(推荐md/txt)

- `persona_plus update <文件夹/人格ID>`
  - 更新现有人格。发送此命令后，请直接在聊天中发送新的文本 System Prompt，或者文本文件(推荐md/txt)

- `persona_plus avatar <文件夹/人格ID>`
  - 上传或更新人格头像。发送此命令后，请在聊天中发送图片，插件会保存头像并在配置允许时尝试同步到 QQ

- `persona_plus delete <文件夹/人格ID>`
  - 删除指定人格(管理员权限)

#### 注意事项
- 大多数情况下，可以省略人设文件路径，只写人格ID作为指令参数
- 使用`"xx/"`表示文件夹,例如`pp create 测试/1234`指令会在`测试`文件夹中创建名为`1234`的人格，其他指令同理

### 配置项
- 启用关键词切换(enable_keyword_switching)
  - 是否启用关键词自动切换
  - 默认: true

- 关键词与人格切换映射列表(keyword_mappings)
  - 使用列表配置，每项填写一个`关键词:人格ID`，使用英文冒号分隔
  - 人格 ID 也可以填写文件夹路径，例如 `关键词:测试人格/测试`
  
- 切换作用范围(auto_switch_scope)
  - 人格切换生效范围：`conversation`、`session` 或 `global`。
  - 默认: conversation
   
- 管理指令等待超时时长(manage_wait_timeout_seconds)
  - 创建或更新人格时等待用户发送内容的最长时间(秒)
  - 默认：`60`
  
- 需要管理员权限的指令列表(admin_commands)
  - 使用字典配置，每个指令对应一个布尔值，`true` 表示需要管理员权限，`false` 表示普通用户可用
  - 默认启用管理员权限的指令：create、update、delete、avatar
  - 默认普通用户可用的指令：switch、view、list、help

- 函数工具选项(llm_tool_options)
  - 使用列表细分控制向 LLM 暴露的人格函数工具，按最小权限原则仅开启需要的能力
  - 可选值与能力映射：
    - `list`：查看人格列表
    - `switch`：切换人格
    - `view`：查看人格详情
    - `create`：创建人格
    - `update`：更新人格
    - `delete`：删除人格


- 切换提示(enable_auto_switch_announce)
  - 切换人格时，是否发送提示
  - 默认：开启

- 切换后清空上下文(clear_context_on_switch)
  - 启用后，切换人格后会自动清空当前对话上下文，不需要手动reset
  - 默认：关闭
  
- 修改 QQ 昵称(sync_nickname_on_switch)
  - 是否在切换人格时改变 QQ 昵称(仅适配NapCat!!!)
  - 默认：开启

- 昵称同步模式(nickname_sync_mode)
  - 修改昵称时，使用的模式
    - `profile`: 修改 QQ 昵称，群聊和私聊都会修改 QQ 昵称
    - `group_card`: 群聊中只修改群名片(群昵称)，私聊时不做任何修改
    - `hybrid`: 混合模式 - 群聊中只修改群名片，私聊中修改 QQ 昵称
  - 默认：`group_card`(只修改群昵称)
- 修改 QQ 头像(sync_avatar_on_switch)
  - 是否在切换人格时改变 QQ 头像(仅适配NapCat!!!)
  - 默认：关闭
  
- 昵称模板(nickname_template)
  - 昵称/群名片模板，支持 `{persona_id}` 占位符。
    - 例如：`"[Bot]{persona_id}"` 会将人格 ID 为 "测试" 的昵称设置为 `"[Bot]测试"`
  - 默认: "[B0T]{persona_id}"


### 更新日志
#### ToDo
  - [x] 从文件解析人设
  - [x] 提供tool，让ai可以直接创建/修改人格

#### v1.3.5
  - 修复读取文件设定人设时丢失换行的问题

#### v1.3.4
  - 修复 SenderScopedSessionFilter() takes no arguments 问题
  - 修复 llm tool 调用失败问题

#### v1.3.3
  - **此版本相比`1.3.2`无功能更新，主要是优化代码实现**
  - 优化日志系统
  - 优化代码性能
  - 优化异常处理
  - 优化缓存控制
  - 统一注释风格
  - 移除多余的llm_tools.py文件
  
#### v1.3.2
  - 添加指令的用户识别，防止使用指令时被其他用户消息打断
  - 适配高版本的人格设定文件夹
  - 添加llm tool,便于使用自然语言调用插件功能
  - 更新webui面板配置项
  - 更新help说明文本，自动读取当先指令前缀
  
#### v1.3.1
  - 修复切换人设后，未清理聊天增强历史的问题(开启自动reset的情况下)
  - 拆分代码
#### v1.3
  - 细化权限管理，可针对每一个指令设置权限，统一权限验证
  - 优化代码，减少重复逻辑
  - 修复 windows下无法修改头像的问题，使用base64和file url传输图片
  - 修复 手机对话时，无法正常创建、更新人格的问题（过滤"对方正在输入中"的输入状态）

#### v1.2
  - 从文本文件解析人设
  
#### v1.1
  - 添加插件logo
  - 添加更改群昵称的功能
  
#### v1.0
  - 实现插件基本功能

