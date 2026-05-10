from __future__ import annotations

from collections import defaultdict

from astrbot.api.event import AstrMessageEvent

from .persona_references import PersonaReferenceResolver


def find_folder_tree_node(folder_tree: list[dict], folder_id: str) -> dict | None:
    for node in folder_tree:
        if node.get("folder_id") == folder_id:
            return node
        matched = find_folder_tree_node(node.get("children", []), folder_id)
        if matched is not None:
            return matched
    return None


def collect_folder_ids(folder_tree: list[dict]) -> set[str]:
    folder_ids: set[str] = set()

    for folder in folder_tree:
        folder_id = folder.get("folder_id")
        if folder_id:
            folder_ids.add(folder_id)
        folder_ids.update(collect_folder_ids(folder.get("children", [])))

    return folder_ids


def sort_personas(personas: list) -> list:
    return sorted(
        personas,
        key=lambda persona: (
            getattr(persona, "sort_order", 0),
            str(getattr(persona, "persona_id", "")).casefold(),
        ),
    )


def format_scope_brief(items: list[str] | None) -> str:
    if items is None:
        return "全部"
    if not items:
        return "禁用"
    return f"{len(items)} 项"


def format_scope_detail(items: list[str] | None) -> str:
    if items is None:
        return "全部"
    if not items:
        return "已禁用"
    if len(items) <= 4:
        return "、".join(items)
    preview = "、".join(items[:4])
    return f"{preview} 等 {len(items)} 项"


def build_persona_brief_line(persona) -> str:
    begin_cnt = len(getattr(persona, "begin_dialogs", None) or [])
    tool_text = format_scope_brief(getattr(persona, "tools", None))
    skill_text = format_scope_brief(getattr(persona, "skills", None))
    return f"预设对话 {begin_cnt} 条｜工具 {tool_text}｜技能 {skill_text}"


def build_folder_label(folder_name: str) -> str:
    return f"📁 {folder_name}/"


def build_persona_list_lines(
    persona,
    indent: str = "",
    index: int | None = None,
    is_last: bool = True,
) -> list[str]:
    title = (
        f"[{index}] {persona.persona_id}" if index is not None else persona.persona_id
    )
    branch = "└─ " if is_last else "├─ "
    detail_prefix = indent + ("   " if is_last else "│  ")
    return [
        f"{indent}{branch}{title}",
        f"{detail_prefix}└─ {build_persona_brief_line(persona)}",
    ]


def build_folder_tree_node_output(
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
    lines.append(f"{prefix}{branch}{build_folder_label(folder['name'])}")

    child_prefix = prefix + ("   " if is_last else "│  ")
    folder_personas = sort_personas(personas_by_folder.get(folder["folder_id"], []))
    children = folder.get("children", [])
    child_count = len(folder_personas) + len(children)
    child_index = 0

    for persona in folder_personas:
        child_index += 1
        lines.extend(
            build_persona_list_lines(
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
        child_lines, child_refs, current_index = build_folder_tree_node_output(
            child_folder,
            personas_by_folder,
            prefix=child_prefix,
            is_last=child_index == child_count,
            start_index=current_index,
        )
        lines.extend(child_lines)
        ordered_persona_refs.extend(child_refs)

    return lines, ordered_persona_refs, current_index


def build_folder_tree_output(
    folder_tree: list[dict],
    personas_by_folder: dict[str, list],
    start_index: int = 1,
) -> tuple[list[str], list[str], int]:
    lines: list[str] = []
    ordered_persona_refs: list[str] = []
    current_index = start_index

    for index, folder in enumerate(folder_tree):
        folder_lines, folder_refs, current_index = build_folder_tree_node_output(
            folder,
            personas_by_folder,
            prefix="",
            is_last=index == len(folder_tree) - 1,
            start_index=current_index,
        )
        lines.extend(folder_lines)
        ordered_persona_refs.extend(folder_refs)

    return lines, ordered_persona_refs, current_index


def indent_text_block(text: str, prefix: str = "  ") -> str:
    lines = text.strip().splitlines() or [""]
    return "\n".join(f"{prefix}{line}" if line.strip() else "" for line in lines)


def format_dialog_entry(index: int, role: str, content: str) -> list[str]:
    return [
        f"{index}. {role}",
        indent_text_block(content, prefix="   "),
    ]


class PersonaRenderer:
    """Render persona lists and details for commands and LLM tools."""

    def __init__(self, persona_mgr, resolver: PersonaReferenceResolver):
        self.persona_mgr = persona_mgr
        self.resolver = resolver

    async def render_list(
        self,
        folder_path: str | None = None,
        event: AstrMessageEvent | None = None,
    ) -> str:
        personas = await self.persona_mgr.get_all_personas()
        if not personas:
            return "当前还没有人格，请先创建一个。"

        folder_tree = await self.persona_mgr.get_folder_tree()
        target_tree = folder_tree
        header = f"[人格列表] 共 {len(personas)} 个"

        if folder_path:
            normalized_folder_path = folder_path.strip().replace("\\", "/").strip("/")
            folder_parts = [part for part in normalized_folder_path.split("/") if part]
            folder_id = await self.resolver.find_folder_id_by_path(folder_parts)
            folder_node = find_folder_tree_node(folder_tree, folder_id)
            if folder_node is None:
                raise ValueError(f"未找到文件夹路径：{folder_path}")

            target_tree = [folder_node]
            visible_folder_ids = collect_folder_ids(target_tree)
            visible_persona_count = sum(
                1
                for persona in personas
                if getattr(persona, "folder_id", None) in visible_folder_ids
            )
            header = (
                f"[人格列表] {normalized_folder_path or '根目录'}"
                f"（{visible_persona_count} 个）"
            )

        personas_by_folder: dict[str, list] = defaultdict(list)
        for persona in personas:
            folder_id = getattr(persona, "folder_id", None)
            if folder_id:
                personas_by_folder[folder_id].append(persona)

        root_personas: list = []
        if not folder_path:
            root_personas = sort_personas(
                [persona for persona in personas if persona.folder_id is None]
            )

        lines = [header]
        ordered_persona_refs: list[str] = []
        if folder_path:
            tree_lines, tree_persona_refs, _next_index = build_folder_tree_output(
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
            lines.extend(["", build_folder_label("根目录")])
            next_index = 1
            root_child_count = len(root_personas) + len(target_tree)
            root_child_index = 0

            for persona in root_personas:
                root_child_index += 1
                lines.extend(
                    build_persona_list_lines(
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
                folder_lines, folder_refs, next_index = build_folder_tree_node_output(
                    folder,
                    personas_by_folder,
                    prefix="",
                    is_last=root_child_index == root_child_count,
                    start_index=next_index,
                )
                lines.extend(folder_lines)
                ordered_persona_refs.extend(folder_refs)

        self.resolver.cache_listed_personas(event, ordered_persona_refs)
        return "\n".join(lines)

    async def render_detail(
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
        persona = await self.persona_mgr.get_persona(resolved_persona_id)

        begin_dialogs = persona.begin_dialogs or []
        folder_path = await self.resolver.folder_path_by_id(
            getattr(persona, "folder_id", None)
        )
        custom_error_message = getattr(persona, "custom_error_message", None)

        lines = [
            f"[人格预览] {persona.persona_id}",
            "",
            "[基础信息]",
            f"- 路径：{folder_path}",
            f"- 预设对话：{len(begin_dialogs)} 条",
            f"- 工具：{format_scope_detail(persona.tools)}",
            f"- 技能：{format_scope_detail(persona.skills)}",
            "",
            "[System Prompt]",
            indent_text_block(persona.system_prompt),
        ]

        if begin_dialogs:
            lines.extend(["", "[预设对话]"])
            for idx, dialog in enumerate(begin_dialogs, start=1):
                role = "用户" if idx % 2 == 1 else "助手"
                lines.extend(format_dialog_entry(idx, role, dialog))

        if custom_error_message:
            lines.extend(["", "[错误回复]", indent_text_block(custom_error_message)])

        return "\n".join(lines)
