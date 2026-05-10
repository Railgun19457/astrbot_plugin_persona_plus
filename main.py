from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable, TypeVar

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, MessageEventResult, filter
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.persona_mgr import PersonaManager
from astrbot.core.sentinels import NOT_GIVEN
from astrbot.core.star.star_tools import StarTools
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

from .core.config import PersonaPlusSettings, load_settings
from .core.keyword_switch import match_keyword
from .core.permissions import check_permission
from .core.persona_references import PersonaReferenceResolver
from .core.persona_rendering import PersonaRenderer
from .core.persona_service import PersonaService
from .core.session_flows import SenderScopedSessionFilter, schedule_persona_wait
from .core.switching import switch_persona
from .integrations.qq_profile_sync import QQProfileSync
from .tools import build_llm_tools

ToolResult = TypeVar("ToolResult")


class PersonaPlus(Star):
    """Persona+ plugin entrypoint for lifecycle, commands, and event routing."""

    LLM_TOOL_NAME_BY_OPTION = {
        "list": "persona_plus_list",
        "switch": "persona_plus_switch",
        "view": "persona_plus_view",
        "create": "persona_plus_create",
        "update": "persona_plus_update",
        "export": "persona_plus_export",
        "delete": "persona_plus_delete",
    }
    QUICK_SWITCH_ALIASES = {"pp", "persona_plus", "persona+"}
    KNOWN_SUBCOMMANDS = {
        "help",
        "list",
        "view",
        "delete",
        "create",
        "avatar",
        "update",
        "export",
    }

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.context = context
        self.config = config
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

        self.qq_sync = QQProfileSync(context)
        self._tasks: set[asyncio.Task] = set()

        plugin_data_dir = StarTools.get_data_dir("astrbot_plugin_persona_plus")
        self.persona_data_dir = plugin_data_dir / "persona_files"
        self.persona_export_dir = (
            Path(get_astrbot_temp_path())
            / "astrbot_plugin_persona_plus"
            / "persona_exports"
        )
        self.persona_data_dir.mkdir(parents=True, exist_ok=True)
        self.persona_export_dir.mkdir(parents=True, exist_ok=True)

        self.resolver = PersonaReferenceResolver(self.persona_mgr)
        self.renderer = PersonaRenderer(self.persona_mgr, self.resolver)
        self.persona_service = PersonaService(
            persona_mgr=self.persona_mgr,
            resolver=self.resolver,
            persona_data_dir=self.persona_data_dir,
            persona_export_dir=self.persona_export_dir,
            qq_sync=self.qq_sync,
        )

        self._load_config()
        self._register_llm_tools()

    def _unregister_llm_tools(self) -> None:
        """Remove function tools registered by this plugin."""

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
        """Register Persona+ function tools according to configuration."""

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
        logger.info("Persona+ 权限配置：admin_commands=%s", sorted(self.admin_commands))
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
        """Register function tools after AstrBot core finishes loading."""

        self._register_llm_tools()

    @filter.on_plugin_loaded()
    async def on_plugin_loaded(self, metadata):
        """Register function tools again after this plugin is loaded."""

        if getattr(metadata, "module_path", None) == self.__module__:
            self._register_llm_tools()

    def check_permission(
        self, event: AstrMessageEvent, command: str
    ) -> tuple[bool, str]:
        """Check whether the sender can run a command."""

        return check_permission(
            context=self.context,
            event=event,
            command=command,
            admin_commands=self.admin_commands,
        )

    def _get_command_prefix(self, event: AstrMessageEvent) -> str:
        """Get the visible wake prefix for command help text."""

        wake_prefix = self.context.get_config(event.unified_msg_origin).get(
            "wake_prefix", ["/"]
        )
        if isinstance(wake_prefix, list):
            return next(
                (
                    prefix
                    for prefix in (str(item).strip() for item in wake_prefix)
                    if prefix
                ),
                "/",
            )
        return str(wake_prefix).strip() or "/"

    @staticmethod
    def _safe_reply_template(reply_template: str, persona_id: str) -> str | None:
        """Render an auto-switch reply template safely."""

        try:
            return reply_template.format(persona_id=persona_id)
        except (KeyError, ValueError, IndexError) as exc:
            logger.warning("Persona+ 自动切换提示模板格式错误，回退默认提示：%s", exc)
            return None

    async def _run_llm_tool(
        self,
        action: str,
        runner: Callable[[], Awaitable[ToolResult]],
        failure_message: str,
    ) -> ToolResult | str:
        """Run an LLM tool with consistent error handling."""

        try:
            return await runner()
        except ValueError as exc:
            return str(exc)
        except Exception:  # noqa: BLE001
            logger.exception("Persona+ 函数工具 %s 执行失败", action)
            return failure_message

    async def _switch_persona_by_reference(
        self,
        event: AstrMessageEvent,
        persona_reference: str,
    ) -> str:
        _, resolved_persona_id = await self.resolver.resolve_for_event(
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

    async def _send_export_file(
        self,
        event: AstrMessageEvent,
        persona_reference: str,
    ) -> str:
        async def send_current_session(message_chain: MessageChain) -> bool | None:
            return await self.context.send_message(event.session, message_chain)

        return await self.persona_service.send_export_file(
            event,
            persona_reference,
            send_message=send_current_session,
        )

    async def _switch_persona(
        self,
        event: AstrMessageEvent,
        persona_id: str,
        announce: str | None = None,
    ) -> MessageEventResult | None:
        """Switch the current conversation or configured default persona."""

        _, resolved_persona_id = await self.resolver.resolve_for_event(
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

    def _schedule_persona_wait(
        self,
        event: AstrMessageEvent,
        persona_id: str,
        mode: str,
        folder_id: str | None = None,
    ) -> None:
        def register_task(task: asyncio.Task) -> None:
            self._tasks.add(task)
            task.add_done_callback(lambda done_task: self._tasks.discard(done_task))

        async def create_persona(
            persona_id_: str,
            system_prompt: str,
            begin_dialogs: list | None,
            tools: list | None,
        ) -> None:
            await self.persona_service.create_from_spec(
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
            await self.persona_service.update_from_spec(
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

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_quick_switch_command(self, event: AstrMessageEvent):
        """Handle quick switches such as `/pp <persona_id>`."""

        if not event.is_at_or_wake_command:
            return

        parts = event.get_message_str().strip().split()
        if len(parts) != 2 or parts[0].lower() not in self.QUICK_SWITCH_ALIASES:
            return

        persona_id = parts[1].strip()
        if not persona_id or persona_id.lower() in self.KNOWN_SUBCOMMANDS:
            return

        has_perm, err_msg = self.check_permission(event, "switch")
        if not has_perm:
            yield event.plain_result(err_msg)
            return

        try:
            result = await self._switch_persona(event, persona_id=persona_id)
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return

        if result is not None:
            yield result
            event.stop_event()

    # ==================== Persona management commands ====================
    @filter.command_group("persona_plus", alias={"pp", "persona+"})
    def persona_plus(self):
        """Persona+ command group entrypoint."""
        # The command group itself does not need implementation.

    @persona_plus.command("help")
    async def cmd_help(self, event: AstrMessageEvent):
        """Show Persona+ command help."""

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
            "[说明]",
            "1. 文件夹路径使用 / 分隔",
            "2. 大多数情况下可直接使用人格 ID，不必带文件夹路径",
            "3. list 输出的序号可直接用于切换、view、export、update、avatar、delete",
            "4. create / update 后可直接发送文本，或上传 .txt / .md 等文本文件",
        ]
        yield event.plain_result("\n".join(sections))

    @persona_plus.command("list")
    async def cmd_list(self, event: AstrMessageEvent, folder_path: str | None = None):
        """List registered personas."""

        has_perm, err_msg = self.check_permission(event, "list")
        if not has_perm:
            yield event.plain_result(err_msg)
            return

        try:
            yield event.plain_result(
                await self.renderer.render_list(folder_path, event=event)
            )
        except ValueError as exc:
            yield event.plain_result(str(exc))

    @persona_plus.command("view")
    async def cmd_view(self, event: AstrMessageEvent, persona_id: str):
        """Show persona details."""

        has_perm, err_msg = self.check_permission(event, "view")
        if not has_perm:
            yield event.plain_result(err_msg)
            return

        try:
            yield event.plain_result(
                await self.renderer.render_detail(persona_id, event=event)
            )
        except ValueError as exc:
            yield event.plain_result(str(exc))

    @persona_plus.command("export")
    async def cmd_export(self, event: AstrMessageEvent, persona_id: str):
        """Export a persona system prompt as a file."""

        has_perm, err_msg = self.check_permission(event, "export")
        if not has_perm:
            yield event.plain_result(err_msg)
            return

        try:
            await self._send_export_file(event, persona_id)
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return
        except Exception:  # noqa: BLE001
            logger.exception("Persona+ 导出文件发送失败")
            yield event.plain_result("导出文件发送失败，请稍后重试。")
            return

        event.stop_event()

    @persona_plus.command("delete")
    async def cmd_delete(self, event: AstrMessageEvent, persona_id: str):
        """Delete a persona."""

        has_perm, err_msg = self.check_permission(event, "delete")
        if not has_perm:
            yield event.plain_result(err_msg)
            return

        try:
            result_text = await self.persona_service.delete_by_reference(
                persona_id, event=event
            )
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return

        yield event.plain_result(result_text)

    @persona_plus.command("create")
    async def cmd_create(self, event: AstrMessageEvent, persona_id: str):
        """Create a persona from the next text message or file."""

        has_perm, err_msg = self.check_permission(event, "create")
        if not has_perm:
            yield event.plain_result(err_msg)
            return

        try:
            folder_id, resolved_persona_id = await self.resolver.resolve(
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
            folder_id=folder_id,
        )

    @persona_plus.command("avatar")
    async def cmd_avatar(self, event: AstrMessageEvent, persona_id: str):
        """Upload or update a persona avatar."""

        has_perm, err_msg = self.check_permission(event, "avatar")
        if not has_perm:
            yield event.plain_result(err_msg)
            return

        try:
            _, resolved_persona_id = await self.resolver.resolve_for_event(
                event,
                persona_id,
                require_existing=True,
            )
        except ValueError:
            yield event.plain_result(f"未找到人格 {persona_id}，请先创建该人格。")
            return

        yield event.plain_result("请发送头像图片。")
        self._schedule_persona_wait(event, resolved_persona_id, "avatar")

    @persona_plus.command("update")
    async def cmd_update(self, event: AstrMessageEvent, persona_id: str):
        """Update a persona with content from the next message."""

        has_perm, err_msg = self.check_permission(event, "update")
        if not has_perm:
            yield event.plain_result(err_msg)
            return

        try:
            _, resolved_persona_id = await self.resolver.resolve_for_event(
                event,
                persona_id,
                require_existing=True,
            )
        except ValueError:
            yield event.plain_result(f"未找到人格 {persona_id}，请先创建该人格。")
            return

        yield event.plain_result("请发送新的人设内容，可直接发文本或上传文本文件。")
        self._schedule_persona_wait(event, resolved_persona_id, "update")

    # ==================== Auto-switch listener ====================
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
                mapping.reply_template,
                mapping.persona_id,
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
        """Clean up plugin runtime state."""

        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()
        self.qq_sync.clear_cache()

        logger.info("Persona+ 插件卸载，已清理状态。")
