from __future__ import annotations

from astrbot.api.event import AstrMessageEvent
from astrbot.api import logger

from .persona_index import build_ordered_persona_references


class PersonaReferenceResolver:
    """Resolve persona IDs, folder paths, and global list indexes."""

    def __init__(self, persona_mgr):
        self.persona_mgr = persona_mgr

    @staticmethod
    def normalize_reference(persona_reference: str) -> str:
        return str(persona_reference).strip().replace("\\", "/").strip("/")

    @classmethod
    def split_reference(cls, persona_reference: str) -> tuple[list[str], str]:
        normalized = cls.normalize_reference(persona_reference)
        if not normalized:
            raise ValueError("人格 ID 不能为空。")

        parts = [part for part in normalized.split("/") if part]
        if not parts:
            raise ValueError("人格 ID 不能为空。")

        return parts[:-1], parts[-1]

    async def get_global_index_reference(self, persona_reference: str) -> str | None:
        normalized_reference = str(persona_reference).strip()
        if not normalized_reference.isdigit():
            return None

        personas = await self.persona_mgr.get_all_personas()
        folder_tree = await self.persona_mgr.get_folder_tree()
        ordered_references = build_ordered_persona_references(personas, folder_tree)
        if not ordered_references:
            raise ValueError("当前还没有人格，请先创建一个。")

        index = int(normalized_reference)
        if index < 1 or index > len(ordered_references):
            raise ValueError(
                f"序号超出范围：{index}。当前可用范围是 1 - {len(ordered_references)}。"
            )

        return ordered_references[index - 1]

    async def find_folder_id_by_path(
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

    async def resolve_for_event(
        self,
        event: AstrMessageEvent,
        persona_reference: str,
        *,
        require_existing: bool,
        create_missing_folders: bool = False,
        allow_index: bool = True,
    ) -> tuple[str | None, str]:
        normalized_reference = self.normalize_reference(persona_reference)
        index_reference = None
        if allow_index:
            index_reference = await self.get_global_index_reference(
                normalized_reference
            )

        return await self.resolve(
            index_reference or normalized_reference,
            require_existing=require_existing,
            create_missing_folders=create_missing_folders,
        )

    async def resolve(
        self,
        persona_reference: str,
        *,
        require_existing: bool,
        create_missing_folders: bool = False,
    ) -> tuple[str | None, str]:
        folder_parts, persona_id = self.split_reference(persona_reference)
        folder_id = await self.find_folder_id_by_path(
            folder_parts,
            create_missing=create_missing_folders,
        )

        if not require_existing:
            return folder_id, persona_id

        try:
            persona = await self.persona_mgr.get_persona(persona_id)
        except ValueError as exc:
            raise ValueError(f"未找到人格：{persona_reference}") from exc

        if folder_id is not None and getattr(persona, "folder_id", None) != folder_id:
            raise ValueError(f"未找到人格：{persona_reference}")

        return folder_id, persona_id

    async def folder_path_by_id(self, folder_id: str | None) -> str:
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
