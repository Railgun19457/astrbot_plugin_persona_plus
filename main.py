from __future__ import annotations

import asyncio
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.persona_mgr import PersonaManager
from astrbot.core.star.star_tools import StarTools

from .core.config import PersonaPlusSettings, load_settings
from .core.keyword_switch import match_keyword
from .core.permissions import check_permission
from .core.session_flows import SenderScopedSessionFilter, schedule_persona_wait
from .core.switching import switch_persona
from .integrations.qq_profile_sync import QQProfileSync


class PersonaPlus(Star):
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

        self.qq_sync = QQProfileSync(context)

        self._tasks: set[asyncio.Task] = set()

        # 初始化人格数据目录
        self.persona_data_dir: Path = (
            StarTools.get_data_dir("astrbot_plugin_persona_plus") / "persona_files"
        )
        self.persona_data_dir.mkdir(parents=True, exist_ok=True)
        self._load_config()

    def _load_config(self) -> None:
        self.settings = load_settings(self.config)

        self.keyword_mappings = self.settings.keyword_mappings
        self.auto_switch_scope = self.settings.auto_switch_scope
        self.keyword_switch_enabled = self.settings.keyword_switch_enabled
        self.manage_wait_timeout = self.settings.manage_wait_timeout
        self.admin_commands = self.settings.admin_commands
        self.auto_switch_announce = self.settings.auto_switch_announce
        self.clear_context_on_switch = self.settings.clear_context_on_switch

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

    # ==================== 工具函数 ====================
    def check_permission(
        self, event: AstrMessageEvent, command: str
    ) -> tuple[bool, str]:
        """统一的权限检查函数。

        Args:
            event: 消息事件
            command: 指令名称 (help, list, view, create, update, delete, avatar)

        Returns:
            (是否有权限, 错误提示信息)
            - (True, "") - 有权限
            - (False, "错误信息") - 无权限
        """
        return check_permission(
            context=self.context,
            event=event,
            command=command,
            admin_commands=self.admin_commands,
        )

    @staticmethod
    def _parse_persona_payload(raw_text: str) -> tuple[str, list]:
        """将用户传入的全部文本作为 system_prompt"""
        return raw_text, []

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
        all_personas: list,
        depth: int = 0,
    ) -> list[str]:
        lines: list[str] = []
        prefix = "│ " * depth

        for folder in folder_tree:
            lines.append(f"{prefix}├ 📁 {folder['name']}/")

            folder_personas = [
                persona
                for persona in all_personas
                if persona.folder_id == folder["folder_id"]
            ]
            child_prefix = "│ " * (depth + 1)

            for persona in folder_personas:
                tool_cnt = "ALL" if persona.tools is None else len(persona.tools)
                skill_cnt = "ALL" if persona.skills is None else len(persona.skills)
                lines.append(
                    f"{child_prefix}├ 👤 {persona.persona_id} | 工具: {tool_cnt} | Skills: {skill_cnt}"
                )

            children = folder.get("children", [])
            if children:
                lines.extend(
                    PersonaPlus._build_folder_tree_output(
                        children,
                        all_personas,
                        depth + 1,
                    )
                )

        return lines

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
            await self._create_persona(
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
            await self.persona_mgr.update_persona(
                persona_id=persona_id_,
                system_prompt=system_prompt,
                begin_dialogs=begin_dialogs if begin_dialogs else None,
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
            session_filter=SenderScopedSessionFilter(event),
        )
        return

    async def _create_persona(
        self,
        persona_id: str,
        system_prompt: str,
        begin_dialogs: list | None,
        folder_id: str | None = None,
        tools: list | None = None,
    ):
        """创建新人格"""
        try:
            await self.persona_mgr.get_persona(persona_id)
        except ValueError:
            await self.persona_mgr.create_persona(
                persona_id=persona_id,
                system_prompt=system_prompt,
                begin_dialogs=begin_dialogs if begin_dialogs else None,
                folder_id=folder_id,
                tools=tools,
            )
            logger.info("Persona+ 已创建人格 %s", persona_id)
        else:
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

        _, resolved_persona_id = await self._resolve_persona_reference(
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

        sections = [
            "Persona+ 扩展指令(/persona_plus /pp /persona+ 可用)：",
            "- /persona_plus 人格ID 或 文件夹/人格ID — 切换到指定人格",
            "- /persona_plus help — 查看帮助与配置说明",
            "- /persona_plus list [文件夹路径] — 列出所有人格或指定文件夹下的人格",
            "- /persona_plus view <人格ID 或 文件夹/人格ID> — 查看人格详情",
            "- /persona_plus create <文件夹/人格ID> — 创建新人格，随后发送文本内容或上传文本文件",
            "- /persona_plus update <人格ID 或 文件夹/人格ID> — 更新人格，随后发送文本内容或上传文本文件",
            "- /persona_plus avatar <人格ID 或 文件夹/人格ID> — 上传人格头像，随后发送图片",
            "- /persona_plus delete <人格ID 或 文件夹/人格ID> — 删除人格 (管理员)",
            "",
            "提示：创建/更新/头像上传时，只会接收最初发起指令的用户后续发送内容。",
            "提示：文件夹路径使用 / 分隔，例如：/persona_plus create 测试人格/测试。",
            "提示：创建/更新人格时，可以直接发送文本，或上传 .txt/.md 等文本文件。",
        ]
        yield event.plain_result("\n".join(sections))

    @persona_plus.command("list")
    async def cmd_list(self, event: AstrMessageEvent, folder_path: str | None = None):
        """列出所有已注册人格。"""

        has_perm, err_msg = self.check_permission(event, "list")
        if not has_perm:
            yield event.plain_result(err_msg)
            return

        personas = await self.persona_mgr.get_all_personas()
        if not personas:
            yield event.plain_result("当前没有人格，请先在控制台或通过指令创建。")
            return

        folder_tree = await self.persona_mgr.get_folder_tree()
        target_tree = folder_tree
        header = "已载入人格："

        if folder_path:
            normalized_folder_path = folder_path.strip().replace("\\", "/").strip("/")
            try:
                folder_parts = [
                    part for part in normalized_folder_path.split("/") if part
                ]
                folder_id = await self._find_folder_id_by_path(folder_parts)
            except ValueError as exc:
                yield event.plain_result(str(exc))
                return

            folder_node = self._find_folder_tree_node(folder_tree, folder_id)
            if folder_node is None:
                yield event.plain_result(f"未找到文件夹路径：{folder_path}")
                return

            target_tree = [folder_node]
            header = f"文件夹 {normalized_folder_path or '根目录'} 下的人格："

        lines = [header]
        tree_lines = self._build_folder_tree_output(target_tree, personas)
        if tree_lines:
            lines.extend(tree_lines)
        elif not folder_path:
            lines.append("- 当前没有已组织的文件夹人格")

        if not folder_path:
            root_personas = [p for p in personas if p.folder_id is None]
            if root_personas:
                if tree_lines:
                    lines.append("")
                for persona in root_personas:
                    begin_cnt = len(persona.begin_dialogs or [])
                    tool_cnt = (
                        len(persona.tools or []) if persona.tools is not None else "ALL"
                    )
                    skill_cnt = (
                        len(persona.skills or [])
                        if persona.skills is not None
                        else "ALL"
                    )
                    lines.append(
                        f"👤 {persona.persona_id} | 预设对话: {begin_cnt} | 工具: {tool_cnt} | Skills: {skill_cnt}"
                    )

        lines.append(f"\n共 {len(personas)} 个人格")
        yield event.plain_result("\n".join(lines))

    @persona_plus.command("view")
    async def cmd_view(self, event: AstrMessageEvent, persona_id: str):
        """查看指定人格详情。"""

        has_perm, err_msg = self.check_permission(event, "view")
        if not has_perm:
            yield event.plain_result(err_msg)
            return

        try:
            _, resolved_persona_id = await self._resolve_persona_reference(
                persona_id,
                require_existing=True,
            )
            persona = await self.persona_mgr.get_persona(resolved_persona_id)
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return

        begin_dialogs = persona.begin_dialogs or []
        tools = persona.tools

        lines = [
            f"人格 {persona.persona_id}",
            "----------------",
            "System Prompt:",
            persona.system_prompt,
        ]

        if begin_dialogs:
            lines.append("\n预设对话：")
            for idx, dialog in enumerate(begin_dialogs, start=1):
                role = "用户" if idx % 2 == 1 else "助手"
                lines.append(f"[{role}] {dialog}")

        if tools is None:
            lines.append("\n工具：使用全部可用工具")
        elif len(tools) == 0:
            lines.append("\n工具：已禁用所有工具")
        else:
            lines.append("\n工具：" + ", ".join(tools))

        yield event.plain_result("\n".join(lines))

    @persona_plus.command("delete")
    async def cmd_delete(self, event: AstrMessageEvent, persona_id: str):
        """删除指定人格。"""

        has_perm, err_msg = self.check_permission(event, "delete")
        if not has_perm:
            yield event.plain_result(err_msg)
            return

        try:
            _, resolved_persona_id = await self._resolve_persona_reference(
                persona_id,
                require_existing=True,
            )
            await self.persona_mgr.delete_persona(resolved_persona_id)
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return

        self.qq_sync.delete_avatar(resolved_persona_id)
        yield event.plain_result(f"人格 {resolved_persona_id} 已删除。")

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

        try:
            await self.persona_mgr.get_persona(resolved_persona_id)
        except ValueError:
            pass
        else:
            yield event.plain_result(
                f"人格 {resolved_persona_id} 已存在，请使用 /persona_plus update {persona_id}。"
            )
            return

        yield event.plain_result("请发送人格内容(文本消息或文本文件)")
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
            _, resolved_persona_id = await self._resolve_persona_reference(
                persona_id,
                require_existing=True,
            )
            await self.persona_mgr.get_persona(resolved_persona_id)
        except ValueError:
            yield event.plain_result(f"未找到人格 {persona_id}，请先创建该人格。")
            return

        yield event.plain_result("请发送人格头像图片")
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
            _, resolved_persona_id = await self._resolve_persona_reference(
                persona_id,
                require_existing=True,
            )
            await self.persona_mgr.get_persona(resolved_persona_id)
        except ValueError:
            yield event.plain_result(f"未找到人格 {persona_id}，请先创建该人格。")
            return

        yield event.plain_result("请发送新的人格内容(文本消息或文本文件)")
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
            announce = mapping.reply_template.format(persona_id=mapping.persona_id)

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
