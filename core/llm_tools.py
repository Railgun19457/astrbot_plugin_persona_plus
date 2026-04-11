from __future__ import annotations

from typing import Any

from astrbot.api import llm_tool, logger
from astrbot.api.event import AstrMessageEvent

TOOL_NAMES = [
    "persona_plus_list",
    "persona_plus_switch",
    "persona_plus_view",
    "persona_plus_create",
    "persona_plus_update",
    "persona_plus_delete",
]

_PERSONA_PLUS_PLUGIN: Any | None = None


def set_persona_plus_plugin(plugin: Any) -> None:
    global _PERSONA_PLUS_PLUGIN
    _PERSONA_PLUS_PLUGIN = plugin


def clear_persona_plus_plugin() -> None:
    global _PERSONA_PLUS_PLUGIN
    _PERSONA_PLUS_PLUGIN = None


def _require_plugin() -> Any:
    if _PERSONA_PLUS_PLUGIN is None:
        raise ValueError("Persona+ 插件尚未初始化，无法使用函数工具。")
    return _PERSONA_PLUS_PLUGIN


@llm_tool(name="persona_plus_list")
async def persona_plus_list(event: AstrMessageEvent, folder_path: str = ""):
    """查看人设列表。

    Args:
        folder_path(string): 可选的文件夹路径；留空表示列出全部人设。
    """

    plugin = _require_plugin()
    try:
        return await plugin._render_persona_list_text(folder_path or None)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Persona+ 函数工具 list 执行失败")
        return f"查看人设列表失败：{exc}"


@llm_tool(name="persona_plus_switch")
async def persona_plus_switch(event: AstrMessageEvent, persona_reference: str):
    """切换人设。

    Args:
        persona_reference(string): 人设 ID，或 文件夹/人设ID 路径。
    """

    plugin = _require_plugin()
    try:
        return await plugin._switch_persona_by_reference(event, persona_reference)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Persona+ 函数工具 switch 执行失败")
        return f"切换人设失败：{exc}"


@llm_tool(name="persona_plus_view")
async def persona_plus_view(event: AstrMessageEvent, persona_reference: str):
    """查看人设内容。

    Args:
        persona_reference(string): 人设 ID，或 文件夹/人设ID 路径。
    """

    plugin = _require_plugin()
    try:
        return await plugin._render_persona_detail_text(persona_reference)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Persona+ 函数工具 view 执行失败")
        return f"查看人设内容失败：{exc}"


@llm_tool(name="persona_plus_create")
async def persona_plus_create(
    event: AstrMessageEvent,
    persona_reference: str,
    system_prompt: str,
):
    """创建人设。

    Args:
        persona_reference(string): 人设 ID，或 文件夹/人设ID 路径。
        system_prompt(string): 人设的 System Prompt。
    """

    plugin = _require_plugin()
    try:
        return await plugin._create_persona_by_reference(
            persona_reference, system_prompt
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Persona+ 函数工具 create 执行失败")
        return f"创建人设失败：{exc}"


@llm_tool(name="persona_plus_update")
async def persona_plus_update(
    event: AstrMessageEvent,
    persona_reference: str,
    system_prompt: str,
):
    """更新人设。

    Args:
        persona_reference(string): 人设 ID，或 文件夹/人设ID 路径。
        system_prompt(string): 新的人设 System Prompt。
    """

    plugin = _require_plugin()
    try:
        return await plugin._update_persona_by_reference(
            persona_reference, system_prompt
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Persona+ 函数工具 update 执行失败")
        return f"更新人设失败：{exc}"


@llm_tool(name="persona_plus_delete")
async def persona_plus_delete(event: AstrMessageEvent, persona_reference: str):
    """删除人设。

    Args:
        persona_reference(string): 人设 ID，或 文件夹/人设ID 路径。
    """

    plugin = _require_plugin()
    try:
        return await plugin._delete_persona_by_reference(persona_reference)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Persona+ 函数工具 delete 执行失败")
        return f"删除人设失败：{exc}"
