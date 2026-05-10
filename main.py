from __future__ import annotations

import asyncio
import hashlib
import re
from collections import defaultdict
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.persona_mgr import PersonaManager
from astrbot.core.sentinels import NOT_GIVEN
from astrbot.core.star.star_tools import StarTools
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

from .core.config import PersonaPlusSettings, load_settings
from .core.keyword_switch import match_keyword
from .core.permissions import check_permission
from .core.session_flows import SenderScopedSessionFilter, schedule_persona_wait
from .core.switching import switch_persona
from .integrations.qq_profile_sync import QQProfileSync
from .tools import build_llm_tools


class PersonaPlus(Star):
    """Persona+ 插件主入口，负责命令、自动切换和 LLM 工具编排。"""

    LLM_TOOL_NAME_BY_OPTION = {
        "list": "persona_plus_list",
        "switch": "persona_plus_switch",
        "view": "persona_plus_view",
        "create": "persona_plus_create",
        "update": "persona_plus_update",
        "export": "persona_plus_export",
        "delete": "persona_plus_delete",
    }

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.context: Context = context
        self.config: AstrBotConfig | None = config
        self.persona_mgr: PersonaManager = context.persona_manager

        self.settings: PersonaPlusSettings
        self.keyword_mappings = []
        self.auto_switch_scope = "conversation"
        self.keyword_switch_enabled = True
        self.manage_wait_timeout = 60
        self.admin_commands: set[str] = set()
        self.auto_switch_announce = True
        self.clear_context_on_switch = False
        self.llm_tool_options: set[str] = set()
        self._persona_list_index_cache: dict[str, list[str]] = {}

        self.qq_sync = QQProfileSync(context)
        self._tasks: set[asyncio.Task] = set()

        plugin_data_dir = StarTools.get_data_dir("astrbot_plugin_persona_plus")
        self.persona_data_dir: Path = plugin_data_dir / "persona_files"
        self.persona_data_dir.mkdir(parents=True, exist_ok=True)
        self.persona_export_dir: Path = (
            Path(get_astrbot_temp_path())
            / "astrbot_plugin_persona_plus"
            / "persona_exports"
        )
        self.persona_export_dir.mkdir(parents=True, exist_ok=True)
        self._load_config()
        self._register_llm_tools()

    def _unregister_llm_tools(self) -> None:
        """移除本插件注册的函数工具，避免关闭配置后仍出现在工具列表。"""

        tool_mgr = self.context.get_llm_tool_manager()
        persona_plus_tool_names = set(self.LLM_TOOL_NAME_BY_OPTION.values())
        tool_mgr.func_list = [
            tool
            for tool in tool_mgr.func_list
            if not (
                tool.name in persona_plus_tool_names
                and getattr(tool, "handler_module_path", None) == self.__module__
            )
        ]

    def _register_llm_tools(self) -> None:
        """按配置注册 Persona+ 函数工具。"""

        self._unregister_llm_tools()
        if not self.llm_tool_options:
            logger.info("Persona+ 函数工具未启用，跳过注册。")
            return

        enabled_tool_names = {
            self.LLM_TOOL_NAME_BY_OPTION[key]
            for key in self.llm_tool_options
            if key in self.LLM_TOOL_NAME_BY_OPTION
        }
        tools = [
            tool for tool in build_llm_tools(self) if tool.name in enabled_tool_names
        ]
        if tools:
            self.context.add_llm_tools(*tools)

        logger.info(
            "Persona+ 函数工具配置：选项=%s，已注册=%s",
            sorted(self.llm_tool_options),
            sorted(tool.name for tool in tools),
        )

    def _load_config(self) -> None:
        self.settings = load_settings(self.config)

        self.keyword_mappings = self.settings.keyword_mappings
        self.auto_switch_scope = self.settings.auto_switch_scope
        self.keyword_switch_enabled = self.settings.keyword_switch_enabled
        self.manage_wait_timeout = self.settings.manage_wait_timeout
        self.admin_commands = self.settings.admin_commands
        self.auto_switch_announce = self.settings.auto_switch_announce
        self.clear_context_on_switch = self.settings.clear_context_on_switch
        self.llm_tool_options = self.settings.llm_tool_options

        self.qq_sync.load_config(self.config)

        logger.info(
            "Persona+ 配置加载完成：关键词 %d 项，自动切换范围=%s，关键词自动切换=%s，QQ同步=%s",
            len(self.keyword_mappings),
            self.auto_switch_scope,
            self.keyword_switch_enabled,
            self.qq_sync.describe_settings(),
        )
        logger.info(
            "Persona+ 权限配置：admin_commands=%s",
            sorted(self.admin_commands),
        )
        logger.info(
            "Persona+ 管理操作等待超时：manage_wait_timeout=%ss",
            self.manage_wait_timeout,
        )
        logger.info(
            "Persona+ 自动切换提示：enable_auto_switch_announce=%s",
            self.auto_switch_announce,
        )
        logger.info(
            "Persona+ 切换后清空上下文：clear_context_on_switch=%s",
            self.clear_context_on_switch,
        )

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self):
        """核心加载完成后重新注册函数工具。"""

        self._register_llm_tools()

    @filter.on_plugin_loaded()
    async def on_plugin_loaded(self, metadata):
        """插件加载后重新注册本插件函数工具，避免被旧状态覆盖。"""

        if getattr(metadata, "module_path", None) == self.__module__:
            self._register_llm_tools()

    def check_permission(
        self, event: AstrMessageEvent, command: str
    ) -> tuple[bool, str]:
        """统一的权限检查函数。"""

        return check_permission(
            context=self.context,
            event=event,
            command=command,
            admin_commands=self.admin_commands,
        )

    def _get_command_prefix(self, event: AstrMessageEvent) -> str:
        """读取当前会话可见的唤醒前缀，用于帮助文案展示。"""

        conf = self.context.get_config(event.unified_msg_origin)
        wake_prefix = conf.get("wake_prefix", ["/"])
        if isinstance(wake_prefix, list):
            for prefix in wake_prefix:
                prefix_text = str(prefix).strip()
                if prefix_text:
                    return prefix_text
            return "/"
        prefix_text = str(wake_prefix).strip()
        return prefix_text or "/"

    @staticmethod
    def _parse_persona_payload(raw_text: str) -> tuple[str, list]:
        """将用户传入的全部文本作为 system_prompt。"""
        return raw_text, []

    @staticmethod
    def _safe_reply_template(reply_template: str, persona_id: str) -> str | None:
        """安全渲染自动切换提示模板，模板非法时返回 None。"""

        try:
            return reply_template.format(persona_id=persona_id)
        except (KeyError, ValueError, IndexError) as exc:
            logger.warning("Persona+ 自动切换提示模板格式错误，回退默认提示：%s", exc)
            return None

    async def _run_llm_tool(self, action: str, runner, failure_message: str):
        """统一 LLM 工具的异常处理和兜底提示。"""

        try:
            return await runner()
        except ValueError as exc:
            return str(exc)
        except Exception:  # noqa: BLE001
            logger.exception("Persona+ 函数工具 %s 执行失败", action)
            return failure_message

    @staticmethod
    def _safe_export_file_stem(persona_id: str) -> str:
        stem = re.sub(r"[^\w.-]+", "_", persona_id, flags=re.UNICODE).strip("._")
        return (stem or "persona")[:80]

    def _build_persona_export_path(self, persona_id: str) -> Path:
        digest = hashlib.sha1(persona_id.encode("utf-8")).hexdigest()[:8]
        return self.persona_export_dir / (
            f"{self._safe_export_file_stem(persona_id)}_{digest}.md"
        )

    async def _write_persona_export_file(self, persona) -> Path:
        persona_id = str(getattr(persona, "persona_id", "") or "persona")
        system_prompt = str(getattr(persona, "system_prompt", "") or "").strip()
        if not system_prompt:
            raise ValueError(f"人格 {persona_id} 的 System Prompt 为空，无法导出。")

        export_path = self._build_persona_export_path(persona_id)
        export_content = f"{system_prompt.rstrip()}\n"
        await asyncio.to_thread(
            export_path.write_text, export_content, encoding="utf-8"
        )
        logger.info("Persona+ 已导出人格 %s 至 %s", persona_id, export_path)
        return export_path

    async def _export_persona_file_by_reference(
        self,
        persona_reference: str,
        event: AstrMessageEvent | None = None,
    ) -> tuple[str, Path]:
        if event is None:
            _, resolved_persona_id = await self._resolve_persona_reference(
                persona_reference,
                require_existing=True,
            )
        else:
            _, resolved_persona_id = await self._resolve_persona_reference_for_event(
                event,
                persona_reference,
                require_existing=True,
            )

        persona = await self.persona_mgr.get_persona(resolved_persona_id)
        export_path = await self._write_persona_export_file(persona)
        return resolved_persona_id, export_path

    @staticmethod
    def _build_persona_export_result(
        event: AstrMessageEvent,
        persona_id: str,
        export_path: Path,
    ) -> MessageEventResult:
        return event.chain_result(
            [
                Comp.Plain(f"人格 {persona_id} 已导出为文件：{export_path.name}"),
                Comp.File(name=export_path.name, file=str(export_path)),
            ]
        )

    async def _send_persona_export_file(
        self,
        event: AstrMessageEvent,
        persona_reference: str,
    ) -> str:
        resolved_persona_id, export_path = await self._export_persona_file_by_reference(
            persona_reference,
            event=event,
        )
        await event.send(
            self._build_persona_export_result(event, resolved_persona_id, export_path)
        )
        return f"人格 {resolved_persona_id} 已导出并发送为文件 {export_path.name}。"

    @staticmethod
    def _normalize_persona_reference(persona_reference: str) -> str:
        return persona_reference.strip().replace("\\", "/").strip("/")

    @staticmethod
    def _split_persona_reference(persona_reference: str) -> tuple[list[str], str]:
        normalized = PersonaPlus._normalize_persona_reference(persona_reference)
        if not normalized:
            raise ValueError("人格 ID 不能为空。")

        parts = [part for part in normalized.split("/") if part]
        if not parts:
            raise ValueError("人格 ID 不能为空。")

        return parts[:-1], parts[-1]

    @staticmethod
    def _normalize_system_prompt(system_prompt) -> str:
        system_prompt_text = "" if system_prompt is None else str(system_prompt).strip()
        if not system_prompt_text:
            raise ValueError("system_prompt 不能为空。")
        return system_prompt_text

    @staticmethod
    def _normalize_begin_dialogs(begin_dialogs) -> list[str] | None:
        if begin_dialogs is None:
            return None
        if not isinstance(begin_dialogs, list):
            raise ValueError("begin_dialogs 必须是字符串列表。")

        normalized_dialogs: list[str] = []
        for index, dialog in enumerate(begin_dialogs, start=1):
            dialog_text = str(dialog).strip()
            if not dialog_text:
                raise ValueError(f"begin_dialogs 第 {index} 条不能为空。")
            normalized_dialogs.append(dialog_text)

        if len(normalized_dialogs) % 2 != 0:
            raise ValueError("begin_dialogs 数量必须为偶数（用户和助手轮流对话）。")

        return normalized_dialogs

    @staticmethod
    def _split_scope_text(value: str) -> list[str]:
        text = value
        for separator in ("\r", "\n", "，", "、", ";", "；"):
            text = text.replace(separator, ",")
        return text.split(",")

    @staticmethod
    def _normalize_name_scope(value, field_name: str):
        """规范化工具/技能作用域：None=全部，[]=禁用，列表=仅启用指定项。"""

        if value is NOT_GIVEN or value is None:
            return value

        if isinstance(value, str):
            raw_items = PersonaPlus._split_scope_text(value)
        elif isinstance(value, list):
            raw_items = value
        else:
            raise ValueError(f"{field_name} 必须是字符串列表、逗号分隔文本或 null。")

        normalized_items: list[str] = []
        for item in raw_items:
            item_text = str(item).strip()
            if item_text and item_text not in normalized_items:
                normalized_items.append(item_text)

        return normalized_items

    @staticmethod
    def _normalize_custom_error_message(value):
        if value is NOT_GIVEN or value is None:
            return value
        message = str(value).strip()
        return message or None

    async def _create_persona_from_spec(
        self,
        *,
        persona_id: str,
        system_prompt: str,
        begin_dialogs: list | None = None,
        folder_id: str | None = None,
        tools: list | None = None,
        skills: list | None = None,
        custom_error_message: str | None = None,
    ):
        """统一创建人格，供指令等待流和 LLM Tool 共同调用。"""

        system_prompt_text = self._normalize_system_prompt(system_prompt)
        normalized_dialogs = self._normalize_begin_dialogs(begin_dialogs)
        normalized_tools = self._normalize_name_scope(tools, "tools")
        normalized_skills = self._normalize_name_scope(skills, "skills")
        normalized_error_message = self._normalize_custom_error_message(
            custom_error_message
        )

        await self._ensure_persona_absent(persona_id)
        await self.persona_mgr.create_persona(
            persona_id=persona_id,
            system_prompt=system_prompt_text,
            begin_dialogs=normalized_dialogs if normalized_dialogs else None,
            folder_id=folder_id,
            tools=normalized_tools,
            skills=normalized_skills,
            custom_error_message=normalized_error_message,
        )
        logger.info("Persona+ 已创建人格 %s", persona_id)

    async def _update_persona_from_spec(
        self,
        *,
        persona_id: str,
        system_prompt=NOT_GIVEN,
        begin_dialogs=NOT_GIVEN,
        tools=NOT_GIVEN,
        skills=NOT_GIVEN,
        custom_error_message=NOT_GIVEN,
    ):
        """统一更新人格，未传入的字段保持不变。"""

        update_kwargs = {"persona_id": persona_id}

        if system_prompt is not NOT_GIVEN:
            update_kwargs["system_prompt"] = self._normalize_system_prompt(
                system_prompt
            )

        if begin_dialogs is not NOT_GIVEN:
            normalized_dialogs = self._normalize_begin_dialogs(begin_dialogs)
            update_kwargs["begin_dialogs"] = normalized_dialogs or []

        if tools is not NOT_GIVEN:
            update_kwargs["tools"] = self._normalize_name_scope(tools, "tools")

        if skills is not NOT_GIVEN:
            update_kwargs["skills"] = self._normalize_name_scope(skills, "skills")

        if custom_error_message is not NOT_GIVEN:
            update_kwargs["custom_error_message"] = (
                self._normalize_custom_error_message(custom_error_message)
            )

        if len(update_kwargs) == 1:
            raise ValueError("没有提供需要更新的内容。")

        await self.persona_mgr.update_persona(**update_kwargs)

    async def _update_persona_by_reference(
        self,
        persona_reference: str,
        system_prompt=NOT_GIVEN,
        begin_dialogs=NOT_GIVEN,
        tools=NOT_GIVEN,
        skills=NOT_GIVEN,
        custom_error_message=NOT_GIVEN,
        event: AstrMessageEvent | None = None,
    ) -> str:
        if event is None:
            _, resolved_persona_id = await self._resolve_persona_reference(
                persona_reference,
                require_existing=True,
            )
        else:
            _, resolved_persona_id = await self._resolve_persona_reference_for_event(
                event,
                persona_reference,
                require_existing=True,
            )
        await self._update_persona_from_spec(
            persona_id=resolved_persona_id,
            system_prompt=system_prompt,
            begin_dialogs=begin_dialogs,
            tools=tools,
            skills=skills,
            custom_error_message=custom_error_message,
        )
        return f"人格 {resolved_persona_id} 已更新。"

    async def _delete_persona_by_reference(
        self,
        persona_reference: str,
        event: AstrMessageEvent | None = None,
    ) -> str:
        if event is None:
            _, resolved_persona_id = await self._resolve_persona_reference(
                persona_reference,
                require_existing=True,
            )
        else:
            _, resolved_persona_id = await self._resolve_persona_reference_for_event(
                event,
                persona_reference,
                require_existing=True,
            )
        await self.persona_mgr.delete_persona(resolved_persona_id)
        self._delete_persona_artifacts(resolved_persona_id)
        return f"人格 {resolved_persona_id} 已删除。"

    def _delete_persona_artifacts(self, persona_id: str) -> None:
        self.qq_sync.delete_avatar(persona_id)
        persona_file = self.persona_data_dir / f"{persona_id}.txt"
        if persona_file.exists():
            try:
                persona_file.unlink()
                logger.info("Persona+ 已删除人格 %s 的文本缓存", persona_id)
            except OSError as exc:
                logger.warning("Persona+ 删除人格 %s 文本缓存失败：%s", persona_id, exc)

        export_file = self._build_persona_export_path(persona_id)
        if export_file.exists():
            try:
                export_file.unlink()
                logger.info("Persona+ 已删除人格 %s 的导出文件", persona_id)
            except OSError as exc:
                logger.warning("Persona+ 删除人格 %s 导出文件失败：%s", persona_id, exc)

    async def _switch_persona_by_reference(
        self,
        event: AstrMessageEvent,
        persona_reference: str,
    ) -> str:
        _, resolved_persona_id = await self._resolve_persona_reference_for_event(
            event,
            persona_reference,
            require_existing=True,
        )
        await switch_persona(
            context=self.context,
            persona_mgr=self.persona_mgr,
            qq_sync=self.qq_sync,
            event=event,
            persona_id=resolved_persona_id,
            scope=self.auto_switch_scope,
            clear_context_on_switch=self.clear_context_on_switch,
            announce=None,
        )
        return f"已切换人格为 {resolved_persona_id}"

    def _schedule_persona_wait(
        self,
        event: AstrMessageEvent,
        persona_id: str,
        mode: str,
        folder_id: str | None = None,
    ) -> None:
        def register_task(task: asyncio.Task) -> None:
            self._tasks.add(task)
            task.add_done_callback(lambda t: self._tasks.discard(t))

        async def create_persona(
            persona_id_: str,
            system_prompt: str,
            begin_dialogs: list | None,
            tools: list | None,
        ) -> None:
            await self._create_persona_from_spec(
                persona_id=persona_id_,
                system_prompt=system_prompt,
                begin_dialogs=begin_dialogs,
                tools=tools,
                folder_id=folder_id,
            )

        async def update_persona(
            persona_id_: str,
            system_prompt: str,
            begin_dialogs: list | None,
        ) -> None:
            await self._update_persona_from_spec(
                persona_id=persona_id_,
                system_prompt=system_prompt,
                begin_dialogs=begin_dialogs if begin_dialogs else NOT_GIVEN,
            )

        schedule_persona_wait(
            event=event,
            persona_id=persona_id,
            mode=mode,
            manage_wait_timeout=self.manage_wait_timeout,
            persona_data_dir=self.persona_data_dir,
            qq_sync=self.qq_sync,
            create_persona=create_persona,
            update_persona=update_persona,
            register_task=register_task,
            session_filter=SenderScopedSessionFilter(),
        )
        return

    async def _create_persona(
        self,
        persona_id: str,
        system_prompt: str,
        begin_dialogs: list | None,
        folder_id: str | None = None,
        tools: list | None = None,
        skills: list | None = None,
        custom_error_message: str | None = None,
    ):
        """创建新人格"""
        await self._create_persona_from_spec(
            persona_id=persona_id,
            system_prompt=system_prompt,
            begin_dialogs=begin_dialogs,
            folder_id=folder_id,
            tools=tools,
            skills=skills,
            custom_error_message=custom_error_message,
        )

    async def _ensure_persona_absent(self, persona_id: str) -> None:
        """确认人格不存在，已存在则给出明确提示。"""

        try:
            await self.persona_mgr.get_persona(persona_id)
        except ValueError:
            return
        raise ValueError(
            f"人格 {persona_id} 已存在，请使用 /persona_plus update {persona_id}。"
        )

    async def _switch_persona(
        self,
        event: AstrMessageEvent,
        persona_id: str,
        announce: str | None = None,
    ) -> MessageEventResult | None:
        """切换对话或配置中的默认人格。"""

        _, resolved_persona_id = await self._resolve_persona_reference_for_event(
            event,
            persona_id,
            require_existing=True,
        )

        if announce is None and self.auto_switch_announce:
            announce = f"已切换人格为 {resolved_persona_id}"

        return await switch_persona(
            context=self.context,
            persona_mgr=self.persona_mgr,
            qq_sync=self.qq_sync,
            event=event,
            persona_id=resolved_persona_id,
            scope=self.auto_switch_scope,
            clear_context_on_switch=self.clear_context_on_switch,
            announce=announce,
        )

    async def _find_folder_id_by_path(
        self,
        folder_parts: list[str],
        *,
        create_missing: bool = False,
    ) -> str | None:
        if not folder_parts:
            return None

        folder_id: str | None = None
        for folder_name in folder_parts:
            children = await self.persona_mgr.get_folders(folder_id)
            matched = next(
                (item for item in children if item.name == folder_name), None
            )
            if matched is None:
                if not create_missing:
                    raise ValueError(f"未找到文件夹路径：{'/'.join(folder_parts)}")
                matched = await self.persona_mgr.create_folder(
                    name=folder_name,
                    parent_id=folder_id,
                )
                logger.info(
                    "Persona+ 已自动创建文件夹 %s (parent=%s)",
                    folder_name,
                    folder_id or "root",
                )
            folder_id = matched.folder_id

        return folder_id

    @staticmethod
    def _find_folder_tree_node(folder_tree: list[dict], folder_id: str) -> dict | None:
        for node in folder_tree:
            if node.get("folder_id") == folder_id:
                return node
            matched = PersonaPlus._find_folder_tree_node(
                node.get("children", []),
                folder_id,
            )
            if matched is not None:
                return matched
        return None

    @staticmethod
    def _build_folder_tree_output(
        folder_tree: list[dict],
        personas_by_folder: dict[str, list],
        start_index: int = 1,
    ) -> tuple[list[str], list[str], int]:
        lines: list[str] = []
        ordered_persona_refs: list[str] = []
        current_index = start_index

        for index, folder in enumerate(folder_tree):
            is_last_folder = index == len(folder_tree) - 1
            folder_lines, folder_refs, current_index = (
                PersonaPlus._build_folder_tree_node_output(
                    folder,
                    personas_by_folder,
                    prefix="",
                    is_last=is_last_folder,
                    start_index=current_index,
                )
            )
            lines.extend(folder_lines)
            ordered_persona_refs.extend(folder_refs)

        return lines, ordered_persona_refs, current_index

    @staticmethod
    def _build_folder_tree_node_output(
        folder: dict,
        personas_by_folder: dict[str, list],
        prefix: str,
        is_last: bool,
        start_index: int,
    ) -> tuple[list[str], list[str], int]:
        lines: list[str] = []
        ordered_persona_refs: list[str] = []
        current_index = start_index
        branch = "└─ " if is_last else "├─ "
        lines.append(
            f"{prefix}{branch}{PersonaPlus._build_folder_label(folder['name'])}"
        )

        child_prefix = prefix + ("   " if is_last else "│  ")
        folder_personas = PersonaPlus._sort_personas(
            personas_by_folder.get(folder["folder_id"], [])
        )
        children = folder.get("children", [])
        child_count = len(folder_personas) + len(children)
        child_index = 0

        for persona in folder_personas:
            child_index += 1
            lines.extend(
                PersonaPlus._build_persona_list_lines(
                    persona,
                    child_prefix,
                    current_index,
                    is_last=child_index == child_count,
                )
            )
            ordered_persona_refs.append(persona.persona_id)
            current_index += 1

        for child_folder in children:
            child_index += 1
            child_lines, child_refs, current_index = (
                PersonaPlus._build_folder_tree_node_output(
                    child_folder,
                    personas_by_folder,
                    prefix=child_prefix,
                    is_last=child_index == child_count,
                    start_index=current_index,
                )
            )
            lines.extend(child_lines)
            ordered_persona_refs.extend(child_refs)

        return lines, ordered_persona_refs, current_index

    @staticmethod
    def _collect_folder_ids(folder_tree: list[dict]) -> set[str]:
        folder_ids: set[str] = set()

        for folder in folder_tree:
            folder_id = folder.get("folder_id")
            if folder_id:
                folder_ids.add(folder_id)
            folder_ids.update(
                PersonaPlus._collect_folder_ids(folder.get("children", []))
            )

        return folder_ids

    @staticmethod
    def _sort_personas(personas: list) -> list:
        return sorted(
            personas,
            key=lambda persona: (
                getattr(persona, "sort_order", 0),
                str(getattr(persona, "persona_id", "")).casefold(),
            ),
        )

    @staticmethod
    def _format_scope_brief(items: list[str] | None) -> str:
        if items is None:
            return "全部"
        if not items:
            return "禁用"
        return f"{len(items)} 项"

    @staticmethod
    def _format_scope_detail(items: list[str] | None) -> str:
        if items is None:
            return "全部"
        if not items:
            return "已禁用"
        if len(items) <= 4:
            return "、".join(items)
        preview = "、".join(items[:4])
        return f"{preview} 等 {len(items)} 项"

    @staticmethod
    def _build_persona_brief_line(persona) -> str:
        begin_cnt = len(getattr(persona, "begin_dialogs", None) or [])
        tool_text = PersonaPlus._format_scope_brief(getattr(persona, "tools", None))
        skill_text = PersonaPlus._format_scope_brief(getattr(persona, "skills", None))
        return f"预设对话 {begin_cnt} 条｜工具 {tool_text}｜技能 {skill_text}"

    @staticmethod
    def _build_folder_label(folder_name: str) -> str:
        return f"📁 {folder_name}/"

    @staticmethod
    def _build_persona_list_lines(
        persona,
        indent: str = "",
        index: int | None = None,
        is_last: bool = True,
    ) -> list[str]:
        title = (
            f"[{index}] {persona.persona_id}"
            if index is not None
            else persona.persona_id
        )
        branch = "└─ " if is_last else "├─ "
        detail_prefix = indent + ("   " if is_last else "│  ")
        return [
            f"{indent}{branch}{title}",
            f"{detail_prefix}└─ {PersonaPlus._build_persona_brief_line(persona)}",
        ]

    @staticmethod
    def _indent_text_block(text: str, prefix: str = "  ") -> str:
        lines = text.strip().splitlines() or [""]
        return "\n".join(f"{prefix}{line}" if line.strip() else "" for line in lines)

    @staticmethod
    def _format_dialog_entry(index: int, role: str, content: str) -> list[str]:
        return [
            f"{index}. {role}",
            PersonaPlus._indent_text_block(content, prefix="   "),
        ]

    @staticmethod
    def _build_persona_index_cache_key(event: AstrMessageEvent) -> str:
        return f"{event.unified_msg_origin}:{event.get_sender_id()}"

    def _cache_listed_personas(
        self,
        event: AstrMessageEvent | None,
        persona_references: list[str],
    ) -> None:
        if event is None:
            return

        cache_key = self._build_persona_index_cache_key(event)
        if persona_references:
            self._persona_list_index_cache[cache_key] = persona_references
        else:
            self._persona_list_index_cache.pop(cache_key, None)

    def _get_cached_persona_reference(
        self,
        event: AstrMessageEvent,
        persona_reference: str,
    ) -> str | None:
        normalized_reference = persona_reference.strip()
        if not normalized_reference.isdigit():
            return None

        cache_key = self._build_persona_index_cache_key(event)
        cached_references = self._persona_list_index_cache.get(cache_key)
        if not cached_references:
            return None

        index = int(normalized_reference)
        if index < 1 or index > len(cached_references):
            raise ValueError(
                f"序号超出范围：{index}。当前可用范围是 1 - {len(cached_references)}。"
            )

        return cached_references[index - 1]

    async def _resolve_persona_reference_for_event(
        self,
        event: AstrMessageEvent,
        persona_reference: str,
        *,
        require_existing: bool,
        create_missing_folders: bool = False,
        allow_index: bool = True,
    ) -> tuple[str | None, str]:
        normalized_reference = self._normalize_persona_reference(persona_reference)
        cached_reference = None
        if allow_index:
            cached_reference = self._get_cached_persona_reference(
                event,
                normalized_reference,
            )

        try:
            return await self._resolve_persona_reference(
                cached_reference or normalized_reference,
                require_existing=require_existing,
                create_missing_folders=create_missing_folders,
            )
        except ValueError as exc:
            if (
                allow_index
                and normalized_reference.isdigit()
                and cached_reference is None
            ):
                raise ValueError(
                    f"未找到人格：{normalized_reference}。如需使用序号，请先执行 list 查看当前编号。"
                ) from exc
            raise

    async def _resolve_persona_reference(
        self,
        persona_reference: str,
        *,
        require_existing: bool,
        create_missing_folders: bool = False,
    ) -> tuple[str | None, str]:
        folder_parts, persona_id = self._split_persona_reference(persona_reference)
        folder_id = await self._find_folder_id_by_path(
            folder_parts,
            create_missing=create_missing_folders,
        )

        if not require_existing:
            return folder_id, persona_id

        try:
            persona = await self.persona_mgr.get_persona(persona_id)
        except ValueError as exc:
            if folder_parts:
                raise ValueError(f"未找到人格：{persona_reference}") from exc
            raise

        if folder_id is not None and getattr(persona, "folder_id", None) != folder_id:
            raise ValueError(f"未找到人格：{persona_reference}")

        return folder_id, persona_id

    async def _get_folder_path_by_id(self, folder_id: str | None) -> str:
        if not folder_id:
            return "根目录"

        folders = {
            folder.folder_id: folder
            for folder in await self.persona_mgr.get_all_folders()
        }
        parts: list[str] = []
        current = folders.get(folder_id)
        while current is not None:
            parts.append(current.name)
            current = folders.get(current.parent_id)
        return "/".join(reversed(parts)) if parts else "根目录"

    async def _render_persona_list_text(
        self,
        folder_path: str | None = None,
        event: AstrMessageEvent | None = None,
    ) -> str:
        # 先按文件夹分组，避免在树形渲染时反复扫描全量人格列表。
        personas = await self.persona_mgr.get_all_personas()
        if not personas:
            return "当前还没有人格，请先创建一个。"

        folder_tree = await self.persona_mgr.get_folder_tree()
        target_tree = folder_tree
        visible_personas = personas
        header = f"[人格列表] 共 {len(personas)} 个"

        if folder_path:
            normalized_folder_path = folder_path.strip().replace("\\", "/").strip("/")
            folder_parts = [part for part in normalized_folder_path.split("/") if part]
            folder_id = await self._find_folder_id_by_path(folder_parts)
            folder_node = self._find_folder_tree_node(folder_tree, folder_id)
            if folder_node is None:
                raise ValueError(f"未找到文件夹路径：{folder_path}")

            target_tree = [folder_node]
            visible_folder_ids = self._collect_folder_ids(target_tree)
            visible_personas = [
                persona
                for persona in personas
                if getattr(persona, "folder_id", None) in visible_folder_ids
            ]
            header = (
                f"[人格列表] {normalized_folder_path or '根目录'}"
                f"（{len(visible_personas)} 个）"
            )

        personas_by_folder: dict[str, list] = defaultdict(list)
        for persona in personas:
            folder_id = getattr(persona, "folder_id", None)
            if folder_id:
                personas_by_folder[folder_id].append(persona)

        root_personas: list = []
        if not folder_path:
            root_personas = self._sort_personas(
                [persona for persona in personas if persona.folder_id is None]
            )

        lines = [header]
        ordered_persona_refs: list[str] = []
        if folder_path:
            tree_lines, tree_persona_refs, _next_index = self._build_folder_tree_output(
                target_tree,
                personas_by_folder,
            )
            ordered_persona_refs.extend(tree_persona_refs)
            if tree_lines:
                lines.append("")
                lines.extend(tree_lines)
            else:
                lines.extend(["", "（当前文件夹下还没有人格）"])
        else:
            lines.extend(["", self._build_folder_label("根目录")])
            next_index = 1
            root_child_count = len(root_personas) + len(target_tree)
            root_child_index = 0

            for persona in root_personas:
                root_child_index += 1
                lines.extend(
                    self._build_persona_list_lines(
                        persona,
                        "",
                        next_index,
                        is_last=root_child_index == root_child_count,
                    )
                )
                ordered_persona_refs.append(persona.persona_id)
                next_index += 1

            for folder in target_tree:
                root_child_index += 1
                folder_lines, folder_refs, next_index = (
                    self._build_folder_tree_node_output(
                        folder,
                        personas_by_folder,
                        prefix="",
                        is_last=root_child_index == root_child_count,
                        start_index=next_index,
                    )
                )
                lines.extend(folder_lines)
                ordered_persona_refs.extend(folder_refs)

        self._cache_listed_personas(event, ordered_persona_refs)

        return "\n".join(lines)

    async def _render_persona_detail_text(
        self,
        persona_reference: str,
        event: AstrMessageEvent | None = None,
    ) -> str:
        if event is None:
            _, resolved_persona_id = await self._resolve_persona_reference(
                persona_reference,
                require_existing=True,
            )
        else:
            _, resolved_persona_id = await self._resolve_persona_reference_for_event(
                event,
                persona_reference,
                require_existing=True,
            )
        persona = await self.persona_mgr.get_persona(resolved_persona_id)

        begin_dialogs = persona.begin_dialogs or []
        tools = persona.tools
        skills = persona.skills
        folder_path = await self._get_folder_path_by_id(
            getattr(persona, "folder_id", None)
        )
        custom_error_message = getattr(persona, "custom_error_message", None)
        begin_dialog_count = len(begin_dialogs)

        lines = [
            f"[人格预览] {persona.persona_id}",
            "",
            "[基础信息]",
            f"- 路径：{folder_path}",
            f"- 预设对话：{begin_dialog_count} 条",
            f"- 工具：{self._format_scope_detail(tools)}",
            f"- 技能：{self._format_scope_detail(skills)}",
            "",
            "[System Prompt]",
            self._indent_text_block(persona.system_prompt),
        ]

        if begin_dialogs:
            lines.extend(["", "[预设对话]"])
            for idx, dialog in enumerate(begin_dialogs, start=1):
                role = "用户" if idx % 2 == 1 else "助手"
                lines.extend(self._format_dialog_entry(idx, role, dialog))

        if custom_error_message:
            lines.extend(
                ["", "[错误回复]", self._indent_text_block(custom_error_message)]
            )

        return "\n".join(lines)

    async def _create_persona_by_reference(
        self,
        persona_reference: str,
        system_prompt: str,
        begin_dialogs: list | None = None,
        tools: list | None = None,
        skills: list | None = None,
        custom_error_message: str | None = None,
    ) -> str:
        folder_id, resolved_persona_id = await self._resolve_persona_reference(
            persona_reference,
            require_existing=False,
            create_missing_folders=True,
        )

        await self._create_persona_from_spec(
            persona_id=resolved_persona_id,
            system_prompt=system_prompt,
            begin_dialogs=begin_dialogs,
            folder_id=folder_id,
            tools=tools,
            skills=skills,
            custom_error_message=custom_error_message,
        )

        return f"人格 {resolved_persona_id} 已创建。"

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_quick_switch_command(self, event: AstrMessageEvent):
        """支持 `/pp <persona_id>` 的快捷切换"""

        if not event.is_at_or_wake_command:
            return

        text = event.get_message_str().strip()
        if not text:
            return

        parts = text.split()
        if not parts:
            return

        cmd = parts[0].lower()
        aliases = {"pp", "persona_plus", "persona+"}
        if cmd not in aliases:
            return

        # 形如: pp <persona_id>
        if len(parts) != 2:
            return

        persona_id = parts[1].strip()
        if not persona_id:
            return

        # 如果是已定义的子命令，则忽略，交由指令组处理
        known_subcommands = {
            "help",
            "list",
            "view",
            "delete",
            "create",
            "avatar",
            "update",
            "export",
        }
        if persona_id.lower() in known_subcommands:
            return

        # 验证权限与存在性
        # 快速切换使用默认权限要求（不在 admin_commands 中）
        has_perm, err_msg = self.check_permission(event, "switch")
        if not has_perm:
            yield event.plain_result(err_msg)
            return

        announce = None

        try:
            result = await self._switch_persona(
                event, persona_id=persona_id, announce=announce
            )
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return
        if result is not None:
            yield result
            event.stop_event()

    # ==================== 指令：人格管理 ====================
    @filter.command_group("persona_plus", alias={"pp", "persona+"})
    def persona_plus(self):
        """Persona+ 插件命令入口。"""
        # 指令组不需要实现

    @persona_plus.command("help")
    async def cmd_help(self, event: AstrMessageEvent):
        """展示 Persona+ 指令列表。"""

        has_perm, err_msg = self.check_permission(event, "help")
        if not has_perm:
            yield event.plain_result(err_msg)
            return

        prefix = self._get_command_prefix(event)
        cmd_base = f"{prefix}persona_plus"
        cmd_alias_pp = f"{prefix}pp"
        cmd_alias_plus = f"{prefix}persona+"

        sections = [
            "[Persona+ 指令]",
            f"别名：{cmd_base} / {cmd_alias_pp} / {cmd_alias_plus}",
            "",
            "[快捷切换]",
            f"{cmd_alias_pp} <人格ID>",
            f"{cmd_alias_pp} <文件夹/人格ID>",
            "",
            "[查看与切换]",
            f"{cmd_alias_pp} list [文件夹路径]",
            f"{cmd_alias_pp} view <文件夹/人格ID>",
            f"{cmd_alias_pp} export <文件夹/人格ID>",
            "",
            "[编辑]",
            f"{cmd_alias_pp} create <文件夹/人格ID>",
            f"{cmd_alias_pp} update <文件夹/人格ID>",
            f"{cmd_alias_pp} avatar <文件夹/人格ID>",
            f"{cmd_alias_pp} delete <文件夹/人格ID>  (管理员)",
            "",
            "[示例]",
            f"{cmd_alias_pp} 女仆",
            f"{cmd_alias_pp} view 测试人格/女仆",
            f"{cmd_alias_pp} export 测试人格/女仆",
            f"{cmd_alias_pp} create 测试人格/新角色",
            "",
            "[说明]",
            "1. 文件夹路径使用 / 分隔",
            "2. 大多数情况下可直接使用人格 ID，不必带文件夹路径",
            "3. list 输出的序号可直接用于切换、view、export、update、avatar、delete",
            "4. create / update 后可直接发送文本，或上传 .txt / .md 等文本文件",
        ]
        yield event.plain_result("\n".join(sections))

    @persona_plus.command("list")
    async def cmd_list(self, event: AstrMessageEvent, folder_path: str | None = None):
        """列出所有已注册人格。"""

        has_perm, err_msg = self.check_permission(event, "list")
        if not has_perm:
            yield event.plain_result(err_msg)
            return

        try:
            yield event.plain_result(
                await self._render_persona_list_text(folder_path, event=event)
            )
        except ValueError as exc:
            yield event.plain_result(str(exc))

    @persona_plus.command("view")
    async def cmd_view(self, event: AstrMessageEvent, persona_id: str):
        """查看指定人格详情。"""

        has_perm, err_msg = self.check_permission(event, "view")
        if not has_perm:
            yield event.plain_result(err_msg)
            return

        try:
            yield event.plain_result(
                await self._render_persona_detail_text(persona_id, event=event)
            )
        except ValueError as exc:
            yield event.plain_result(str(exc))

    @persona_plus.command("export")
    async def cmd_export(self, event: AstrMessageEvent, persona_id: str):
        """导出指定人格的 System Prompt 文件。"""

        has_perm, err_msg = self.check_permission(event, "export")
        if not has_perm:
            yield event.plain_result(err_msg)
            return

        try:
            (
                resolved_persona_id,
                export_path,
            ) = await self._export_persona_file_by_reference(
                persona_id,
                event=event,
            )
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return

        yield self._build_persona_export_result(event, resolved_persona_id, export_path)

    @persona_plus.command("delete")
    async def cmd_delete(self, event: AstrMessageEvent, persona_id: str):
        """删除指定人格。"""

        has_perm, err_msg = self.check_permission(event, "delete")
        if not has_perm:
            yield event.plain_result(err_msg)
            return

        try:
            result_text = await self._delete_persona_by_reference(
                persona_id,
                event=event,
            )
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return

        yield event.plain_result(result_text)

    @persona_plus.command("create")
    async def cmd_create(self, event: AstrMessageEvent, persona_id: str):
        """从文本或文件创建新人格。"""

        has_perm, err_msg = self.check_permission(event, "create")
        if not has_perm:
            yield event.plain_result(err_msg)
            return

        try:
            _folder_id, resolved_persona_id = await self._resolve_persona_reference(
                persona_id,
                require_existing=False,
                create_missing_folders=True,
            )
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return

        yield event.plain_result("请发送人设内容，可直接发文本或上传文本文件。")
        self._schedule_persona_wait(
            event,
            resolved_persona_id,
            "create",
            folder_id=_folder_id,
        )
        return

    @persona_plus.command("avatar")
    async def cmd_avatar(self, event: AstrMessageEvent, persona_id: str):
        """上传或更新人格头像。"""

        has_perm, err_msg = self.check_permission(event, "avatar")
        if not has_perm:
            yield event.plain_result(err_msg)
            return

        try:
            _, resolved_persona_id = await self._resolve_persona_reference_for_event(
                event,
                persona_id,
                require_existing=True,
            )
        except ValueError:
            yield event.plain_result(f"未找到人格 {persona_id}，请先创建该人格。")
            return

        yield event.plain_result("请发送头像图片。")
        self._schedule_persona_wait(event, resolved_persona_id, "avatar")
        return

    @persona_plus.command("update")
    async def cmd_update(self, event: AstrMessageEvent, persona_id: str):
        """更新现有人格，使用下一条消息提供内容。"""

        has_perm, err_msg = self.check_permission(event, "update")
        if not has_perm:
            yield event.plain_result(err_msg)
            return

        try:
            _, resolved_persona_id = await self._resolve_persona_reference_for_event(
                event,
                persona_id,
                require_existing=True,
            )
        except ValueError:
            yield event.plain_result(f"未找到人格 {persona_id}，请先创建该人格。")
            return

        yield event.plain_result("请发送新的人设内容，可直接发文本或上传文本文件。")
        self._schedule_persona_wait(event, resolved_persona_id, "update")
        return

    # ==================== 自动切换监听 ====================
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        text = event.get_message_str()
        if not text or not self.keyword_switch_enabled or not self.keyword_mappings:
            return

        mapping = match_keyword(self.keyword_mappings, text)
        if not mapping:
            return

        announce = None
        if mapping.reply_template:
            announce = self._safe_reply_template(
                mapping.reply_template, mapping.persona_id
            )

        try:
            result = await self._switch_persona(
                event,
                persona_id=mapping.persona_id,
                announce=announce,
            )
        except ValueError as exc:
            logger.warning("Persona+ 关键词自动切换失败：%s", exc)
            return
        if result is not None:
            yield result

    async def terminate(self):
        """插件卸载时的清理逻辑。"""

        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()
        self.qq_sync.clear_cache()

        logger.info("Persona+ 插件卸载，已清理状态。")
