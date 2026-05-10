from __future__ import annotations

from astrbot.api.event import AstrMessageEvent
from astrbot.api import logger


class PersonaReferenceResolver:
    """Resolve persona IDs, folder paths, and recent list indexes."""

    def __init__(self, persona_mgr):
        self.persona_mgr = persona_mgr
        self._index_cache: dict[str, list[str]] = {}

    @staticmethod
    def normalize_reference(persona_reference: str) -> str:
        return persona_reference.strip().replace("\\", "/").strip("/")

    @classmethod
    def split_reference(cls, persona_reference: str) -> tuple[list[str], str]:
        normalized = cls.normalize_reference(persona_reference)
        if not normalized:
            raise ValueError("人格 ID 不能为空。")

        parts = [part for part in normalized.split("/") if part]
        if not parts:
            raise ValueError("人格 ID 不能为空。")

        return parts[:-1], parts[-1]

    @staticmethod
    def _index_cache_key(event: AstrMessageEvent) -> str:
        return f"{event.unified_msg_origin}:{event.get_sender_id()}"

    def cache_listed_personas(
        self,
        event: AstrMessageEvent | None,
        persona_references: list[str],
    ) -> None:
        if event is None:
            return

        cache_key = self._index_cache_key(event)
        if persona_references:
            self._index_cache[cache_key] = persona_references
        else:
            self._index_cache.pop(cache_key, None)

    def get_cached_reference(
        self,
        event: AstrMessageEvent,
        persona_reference: str,
    ) -> str | None:
        normalized_reference = persona_reference.strip()
        if not normalized_reference.isdigit():
            return None

        cached_references = self._index_cache.get(self._index_cache_key(event))
        if not cached_references:
            return None

        index = int(normalized_reference)
        if index < 1 or index > len(cached_references):
            raise ValueError(
                f"序号超出范围：{index}。当前可用范围是 1 - {len(cached_references)}。"
            )

        return cached_references[index - 1]

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
        cached_reference = None
        if allow_index:
            cached_reference = self.get_cached_reference(event, normalized_reference)

        try:
            return await self.resolve(
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
            if folder_parts:
                raise ValueError(f"未找到人格：{persona_reference}") from exc
            raise

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
