from __future__ import annotations

from collections import defaultdict


def sort_personas(personas: list) -> list:
    return sorted(
        personas,
        key=lambda persona: (
            getattr(persona, "sort_order", 0),
            str(getattr(persona, "persona_id", "")).casefold(),
        ),
    )


def group_personas_by_folder(personas: list) -> defaultdict[str | None, list]:
    personas_by_folder: defaultdict[str | None, list] = defaultdict(list)
    for persona in personas:
        personas_by_folder[getattr(persona, "folder_id", None)].append(persona)
    return personas_by_folder


def collect_folder_tree_persona_references(
    folder_tree: list[dict],
    personas_by_folder: defaultdict[str | None, list],
) -> list[str]:
    ordered_references: list[str] = []

    for folder in folder_tree:
        folder_id = folder.get("folder_id")
        for persona in sort_personas(personas_by_folder.get(folder_id, [])):
            ordered_references.append(persona.persona_id)
        ordered_references.extend(
            collect_folder_tree_persona_references(
                folder.get("children", []),
                personas_by_folder,
            )
        )

    return ordered_references


def build_ordered_persona_references(
    personas: list,
    folder_tree: list[dict],
    *,
    target_tree: list[dict] | None = None,
) -> list[str]:
    """Build persona references in the same order used by the list command."""

    personas_by_folder = group_personas_by_folder(personas)

    if target_tree is not None:
        return collect_folder_tree_persona_references(target_tree, personas_by_folder)

    ordered_references = [
        persona.persona_id
        for persona in sort_personas(personas_by_folder.get(None, []))
    ]
    ordered_references.extend(
        collect_folder_tree_persona_references(folder_tree, personas_by_folder)
    )
    return ordered_references
