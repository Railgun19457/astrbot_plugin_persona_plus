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
    description: str = "创建新人设并写入 system prompt"
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
        return await plugin._run_llm_tool(
            "create",
            lambda: plugin._create_persona_by_reference(
                persona_reference, system_prompt
            ),
            "创建人设失败，请稍后重试。",
        )


@pydantic_dataclass
class PersonaPlusUpdateTool(_BasePersonaTool):
    name: str = "persona_plus_update"
    description: str = "更新已存在人设的 system prompt"
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
                    "description": "新的人设 System Prompt 全量文本",
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
            return "更新人设失败，请稍后重试。"

        event = self._get_event(context)
        persona_reference = self._as_text(kwargs.get("persona_reference", ""))
        system_prompt = self._as_text(kwargs.get("system_prompt", ""))
        return await plugin._run_llm_tool(
            "update",
            lambda: plugin._update_persona_by_reference(
                persona_reference, system_prompt, event=event
            ),
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
