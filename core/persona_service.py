from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, MessageEventResult
from astrbot.core.sentinels import NOT_GIVEN

from .persona_references import PersonaReferenceResolver


def normalize_system_prompt(system_prompt) -> str:
    system_prompt_text = "" if system_prompt is None else str(system_prompt).strip()
    if not system_prompt_text:
        raise ValueError("system_prompt 不能为空。")
    return system_prompt_text


def normalize_begin_dialogs(begin_dialogs) -> list[str] | None:
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


def split_scope_text(value: str) -> list[str]:
    text = value
    for separator in ("\r", "\n", "，", "、", ";", "；"):
        text = text.replace(separator, ",")
    return text.split(",")


def normalize_name_scope(value, field_name: str):
    """Normalize tool/skill scope: None=all, []=disabled, list=allowlist."""

    if value is NOT_GIVEN or value is None:
        return value

    if isinstance(value, str):
        raw_items = split_scope_text(value)
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


def normalize_custom_error_message(value):
    if value is NOT_GIVEN or value is None:
        return value
    message = str(value).strip()
    return message or None


def safe_export_file_stem(persona_id: str) -> str:
    stem = re.sub(r"[^\w.-]+", "_", persona_id, flags=re.UNICODE).strip("._")
    return (stem or "persona")[:80]


class PersonaService:
    """Create, update, delete, export, and resolve personas."""

    def __init__(
        self,
        *,
        persona_mgr,
        resolver: PersonaReferenceResolver,
        persona_data_dir: Path,
        persona_export_dir: Path,
        qq_sync,
    ):
        self.persona_mgr = persona_mgr
        self.resolver = resolver
        self.persona_data_dir = persona_data_dir
        self.persona_export_dir = persona_export_dir
        self.qq_sync = qq_sync

    def build_export_path(self, persona_id: str) -> Path:
        digest = hashlib.sha1(persona_id.encode("utf-8")).hexdigest()[:8]
        return (
            self.persona_export_dir / f"{safe_export_file_stem(persona_id)}_{digest}.md"
        )

    async def write_export_file(self, persona) -> Path:
        persona_id = str(getattr(persona, "persona_id", "") or "persona")
        system_prompt = str(getattr(persona, "system_prompt", "") or "").strip()
        if not system_prompt:
            raise ValueError(f"人格 {persona_id} 的 System Prompt 为空，无法导出。")

        export_path = self.build_export_path(persona_id)
        await asyncio.to_thread(
            export_path.write_text,
            f"{system_prompt.rstrip()}\n",
            encoding="utf-8",
        )
        logger.info("Persona+ 已导出人格 %s 至 %s", persona_id, export_path)
        return export_path

    async def export_by_reference(
        self,
        persona_reference: str,
        event: AstrMessageEvent | None = None,
    ) -> tuple[str, Path]:
        if event is None:
            _, resolved_persona_id = await self.resolver.resolve(
                persona_reference,
                require_existing=True,
            )
        else:
            _, resolved_persona_id = await self.resolver.resolve_for_event(
                event,
                persona_reference,
                require_existing=True,
            )

        persona = await self.persona_mgr.get_persona(resolved_persona_id)
        export_path = await self.write_export_file(persona)
        return resolved_persona_id, export_path

    @staticmethod
    def build_export_chain(
        persona_id: str,
        export_path: Path,
    ) -> MessageChain:
        return MessageChain(
            chain=[
                Comp.Plain(f"人格 {persona_id} 已导出为文件：{export_path.name}"),
                Comp.File(name=export_path.name, file=str(export_path)),
            ]
        )

    @staticmethod
    def build_export_result(
        event: AstrMessageEvent,
        persona_id: str,
        export_path: Path,
    ) -> MessageEventResult:
        return event.chain_result(
            PersonaService.build_export_chain(persona_id, export_path).chain
        )

    async def send_export_file(
        self,
        event: AstrMessageEvent,
        persona_reference: str,
        send_message: Callable[[MessageChain], Awaitable[bool | None]] | None = None,
    ) -> str:
        resolved_persona_id, export_path = await self.export_by_reference(
            persona_reference,
            event=event,
        )
        chain = self.build_export_chain(resolved_persona_id, export_path)
        if send_message is None:
            await event.send(chain)
        else:
            sent = await send_message(chain)
            if sent is False:
                raise RuntimeError("未找到当前会话对应的平台，文件未发送。")
        return f"人格 {resolved_persona_id} 已导出并发送为文件 {export_path.name}。"

    async def create_from_spec(
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
        system_prompt_text = normalize_system_prompt(system_prompt)
        normalized_dialogs = normalize_begin_dialogs(begin_dialogs)
        normalized_tools = normalize_name_scope(tools, "tools")
        normalized_skills = normalize_name_scope(skills, "skills")
        normalized_error_message = normalize_custom_error_message(custom_error_message)

        await self.ensure_absent(persona_id)
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

    async def update_from_spec(
        self,
        *,
        persona_id: str,
        system_prompt=NOT_GIVEN,
        begin_dialogs=NOT_GIVEN,
        tools=NOT_GIVEN,
        skills=NOT_GIVEN,
        custom_error_message=NOT_GIVEN,
    ):
        update_kwargs = {"persona_id": persona_id}

        if system_prompt is not NOT_GIVEN:
            update_kwargs["system_prompt"] = normalize_system_prompt(system_prompt)

        if begin_dialogs is not NOT_GIVEN:
            normalized_dialogs = normalize_begin_dialogs(begin_dialogs)
            update_kwargs["begin_dialogs"] = normalized_dialogs or []

        if tools is not NOT_GIVEN:
            update_kwargs["tools"] = normalize_name_scope(tools, "tools")

        if skills is not NOT_GIVEN:
            update_kwargs["skills"] = normalize_name_scope(skills, "skills")

        if custom_error_message is not NOT_GIVEN:
            update_kwargs["custom_error_message"] = normalize_custom_error_message(
                custom_error_message
            )

        if len(update_kwargs) == 1:
            raise ValueError("没有提供需要更新的内容。")

        await self.persona_mgr.update_persona(**update_kwargs)

    async def create_by_reference(
        self,
        persona_reference: str,
        system_prompt: str,
        begin_dialogs: list | None = None,
        tools: list | None = None,
        skills: list | None = None,
        custom_error_message: str | None = None,
    ) -> str:
        folder_id, resolved_persona_id = await self.resolver.resolve(
            persona_reference,
            require_existing=False,
            create_missing_folders=True,
        )

        await self.create_from_spec(
            persona_id=resolved_persona_id,
            system_prompt=system_prompt,
            begin_dialogs=begin_dialogs,
            folder_id=folder_id,
            tools=tools,
            skills=skills,
            custom_error_message=custom_error_message,
        )

        return f"人格 {resolved_persona_id} 已创建。"

    async def update_by_reference(
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
            _, resolved_persona_id = await self.resolver.resolve(
                persona_reference,
                require_existing=True,
            )
        else:
            _, resolved_persona_id = await self.resolver.resolve_for_event(
                event,
                persona_reference,
                require_existing=True,
            )
        await self.update_from_spec(
            persona_id=resolved_persona_id,
            system_prompt=system_prompt,
            begin_dialogs=begin_dialogs,
            tools=tools,
            skills=skills,
            custom_error_message=custom_error_message,
        )
        return f"人格 {resolved_persona_id} 已更新。"

    async def delete_by_reference(
        self,
        persona_reference: str,
        event: AstrMessageEvent | None = None,
    ) -> str:
        if event is None:
            _, resolved_persona_id = await self.resolver.resolve(
                persona_reference,
                require_existing=True,
            )
        else:
            _, resolved_persona_id = await self.resolver.resolve_for_event(
                event,
                persona_reference,
                require_existing=True,
            )
        await self.persona_mgr.delete_persona(resolved_persona_id)
        self.delete_artifacts(resolved_persona_id)
        return f"人格 {resolved_persona_id} 已删除。"

    def delete_artifacts(self, persona_id: str) -> None:
        self.qq_sync.delete_avatar(persona_id)
        persona_file = self.persona_data_dir / f"{persona_id}.txt"
        if persona_file.exists():
            try:
                persona_file.unlink()
                logger.info("Persona+ 已删除人格 %s 的文本缓存", persona_id)
            except OSError as exc:
                logger.warning("Persona+ 删除人格 %s 文本缓存失败：%s", persona_id, exc)

        export_file = self.build_export_path(persona_id)
        if export_file.exists():
            try:
                export_file.unlink()
                logger.info("Persona+ 已删除人格 %s 的导出文件", persona_id)
            except OSError as exc:
                logger.warning("Persona+ 删除人格 %s 导出文件失败：%s", persona_id, exc)

    async def ensure_absent(self, persona_id: str) -> None:
        try:
            await self.persona_mgr.get_persona(persona_id)
        except ValueError:
            return
        raise ValueError(
            f"人格 {persona_id} 已存在，请使用 /persona_plus update {persona_id}。"
        )
