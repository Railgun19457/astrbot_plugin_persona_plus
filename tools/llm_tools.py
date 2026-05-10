"""Persona+ 插件的独立 FunctionTool 定义。"""

from __future__ import annotations

from typing import Any

from pydantic import Field
from pydantic.dataclasses import dataclass as pydantic_dataclass

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext


class _BasePersonaTool(FunctionTool[AstrAgentContext]):
    plugin: Any = None

    def _get_event(self, context: ContextWrapper[AstrAgentContext]):
        context_obj = getattr(context, "context", None)
        if context_obj is not None:
            event = getattr(context_obj, "event", None)
            if event is not None:
                return event
        return None

    @staticmethod
    def _as_text(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _optional_list(value: Any, field_name: str) -> list | None:
        if value is None:
            return None
        if not isinstance(value, list):
            raise ValueError(f"{field_name} 必须是数组或 null。")
        return value


@pydantic_dataclass
class PersonaPlusListTool(_BasePersonaTool):
    name: str = "persona_plus_list"
    description: str = "按文件夹范围查询可用人设列表"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "folder_path": {
                    "type": "string",
                    "description": "可选。文件夹路径；留空表示列出全部人设",
                }
            },
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs: Any,
    ) -> ToolExecResult:
        plugin = self.plugin
        if plugin is None:
            return "查看人设列表失败，请稍后重试。"

        folder_path = self._as_text(kwargs.get("folder_path", ""))
        event = self._get_event(context)
        return await plugin._run_llm_tool(
            "list",
            lambda: plugin._render_persona_list_text(folder_path or None, event=event),
            "查看人设列表失败，请稍后重试。",
        )


@pydantic_dataclass
class PersonaPlusSwitchTool(_BasePersonaTool):
    name: str = "persona_plus_switch"
    description: str = "切换当前会话的人设"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "persona_reference": {
                    "type": "string",
                    "description": "目标人设 ID，或 文件夹/人设ID 路径",
                }
            },
            "required": ["persona_reference"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs: Any,
    ) -> ToolExecResult:
        plugin = self.plugin
        event = self._get_event(context)
        if plugin is None or event is None:
            return "切换人设失败，请稍后重试。"

        persona_reference = self._as_text(kwargs.get("persona_reference", ""))
        return await plugin._run_llm_tool(
            "switch",
            lambda: plugin._switch_persona_by_reference(event, persona_reference),
            "切换人设失败，请稍后重试。",
        )


@pydantic_dataclass
class PersonaPlusViewTool(_BasePersonaTool):
    name: str = "persona_plus_view"
    description: str = "查看单个人设的完整详情"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "persona_reference": {
                    "type": "string",
                    "description": "人设 ID，或 文件夹/人设ID 路径",
                }
            },
            "required": ["persona_reference"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs: Any,
    ) -> ToolExecResult:
        plugin = self.plugin
        if plugin is None:
            return "查看人设内容失败，请稍后重试。"

        event = self._get_event(context)
        persona_reference = self._as_text(kwargs.get("persona_reference", ""))
        return await plugin._run_llm_tool(
            "view",
            lambda: plugin._render_persona_detail_text(persona_reference, event=event),
            "查看人设内容失败，请稍后重试。",
        )


@pydantic_dataclass
class PersonaPlusCreateTool(_BasePersonaTool):
    name: str = "persona_plus_create"
    description: str = (
        "创建新人设，并可完整配置预设对话、工具、MCP 工具、Skills 与错误回复"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "persona_reference": {
                    "type": "string",
                    "description": "新人设 ID，或 文件夹/人设ID 路径",
                },
                "system_prompt": {
                    "type": "string",
                    "description": "人设完整 System Prompt 文本",
                },
                "begin_dialogs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选。预设对话文本数组，必须按用户、助手交替排列且数量为偶数",
                },
                "tools": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "description": "可选。允许此人设使用的函数工具名列表；null 或省略表示使用全部工具，空数组表示禁用全部工具。MCP 工具也填写工具名",
                },
                "skills": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "description": "可选。允许此人设使用的 Skills 名称列表；null 或省略表示使用全部 Skills，空数组表示禁用全部 Skills",
                },
                "custom_error_message": {
                    "type": "string",
                    "description": "可选。此人设请求失败时发送给用户的自定义错误回复",
                },
            },
            "required": ["persona_reference", "system_prompt"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs: Any,
    ) -> ToolExecResult:
        plugin = self.plugin
        if plugin is None:
            return "创建人设失败，请稍后重试。"

        persona_reference = self._as_text(kwargs.get("persona_reference", ""))
        system_prompt = self._as_text(kwargs.get("system_prompt", ""))

        async def create_persona():
            begin_dialogs = self._optional_list(
                kwargs.get("begin_dialogs"),
                "begin_dialogs",
            )
            tools = self._optional_list(
                kwargs.get("tools"),
                "tools",
            )
            skills = self._optional_list(
                kwargs.get("skills"),
                "skills",
            )
            custom_error_message = self._optional_text(
                kwargs.get("custom_error_message")
            )
            return await plugin._create_persona_by_reference(
                persona_reference,
                system_prompt,
                begin_dialogs=begin_dialogs,
                tools=tools,
                skills=skills,
                custom_error_message=custom_error_message,
            )

        return await plugin._run_llm_tool(
            "create",
            create_persona,
            "创建人设失败，请稍后重试。",
        )


@pydantic_dataclass
class PersonaPlusUpdateTool(_BasePersonaTool):
    name: str = "persona_plus_update"
    description: str = "更新已存在人设，可修改 system prompt、预设对话、工具、MCP 工具、Skills 与错误回复"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "persona_reference": {
                    "type": "string",
                    "description": "目标人设 ID，或 文件夹/人设ID 路径",
                },
                "system_prompt": {
                    "type": "string",
                    "description": "可选。新的人设 System Prompt 全量文本",
                },
                "begin_dialogs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选。预设对话文本数组，必须按用户、助手交替排列且数量为偶数；省略表示不修改，空数组表示清空",
                },
                "tools": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "description": "可选。允许此人设使用的函数工具名列表；省略表示不修改，null 表示使用全部工具，空数组表示禁用全部工具。MCP 工具也填写工具名",
                },
                "skills": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "description": "可选。允许此人设使用的 Skills 名称列表；省略表示不修改，null 表示使用全部 Skills，空数组表示禁用全部 Skills",
                },
                "custom_error_message": {
                    "type": "string",
                    "description": "可选。此人设请求失败时发送给用户的自定义错误回复；空字符串表示清空",
                },
            },
            "required": ["persona_reference"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs: Any,
    ) -> ToolExecResult:
        plugin = self.plugin
        if plugin is None:
            return "更新人设失败，请稍后重试。"

        event = self._get_event(context)
        persona_reference = self._as_text(kwargs.get("persona_reference", ""))

        async def update_persona():
            update_kwargs: dict[str, Any] = {}
            if "system_prompt" in kwargs:
                update_kwargs["system_prompt"] = self._as_text(
                    kwargs.get("system_prompt")
                )
            if "begin_dialogs" in kwargs:
                update_kwargs["begin_dialogs"] = self._optional_list(
                    kwargs.get("begin_dialogs"),
                    "begin_dialogs",
                )
            if "tools" in kwargs:
                update_kwargs["tools"] = self._optional_list(
                    kwargs.get("tools"),
                    "tools",
                )
            if "skills" in kwargs:
                update_kwargs["skills"] = self._optional_list(
                    kwargs.get("skills"),
                    "skills",
                )
            if "custom_error_message" in kwargs:
                update_kwargs["custom_error_message"] = self._optional_text(
                    kwargs.get("custom_error_message"),
                )
            return await plugin._update_persona_by_reference(
                persona_reference,
                event=event,
                **update_kwargs,
            )

        return await plugin._run_llm_tool(
            "update",
            update_persona,
            "更新人设失败，请稍后重试。",
        )


@pydantic_dataclass
class PersonaPlusDeleteTool(_BasePersonaTool):
    name: str = "persona_plus_delete"
    description: str = "删除指定人设（不可恢复）"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "persona_reference": {
                    "type": "string",
                    "description": "要删除的人设 ID，或 文件夹/人设ID 路径",
                }
            },
            "required": ["persona_reference"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs: Any,
    ) -> ToolExecResult:
        plugin = self.plugin
        if plugin is None:
            return "删除人设失败，请稍后重试。"

        event = self._get_event(context)
        persona_reference = self._as_text(kwargs.get("persona_reference", ""))
        return await plugin._run_llm_tool(
            "delete",
            lambda: plugin._delete_persona_by_reference(persona_reference, event=event),
            "删除人设失败，请稍后重试。",
        )


def build_llm_tools(plugin) -> list[FunctionTool[AstrAgentContext]]:
    tools = [
        PersonaPlusListTool(),
        PersonaPlusSwitchTool(),
        PersonaPlusViewTool(),
        PersonaPlusCreateTool(),
        PersonaPlusUpdateTool(),
        PersonaPlusDeleteTool(),
    ]
    for tool in tools:
        tool.plugin = plugin
    return tools
