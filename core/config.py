from __future__ import annotations

from dataclasses import dataclass

from astrbot.api import logger
from astrbot.core.config.astrbot_config import AstrBotConfig

from .models import KeywordMapping, parse_mapping_entry


@dataclass(slots=True)
class PersonaPlusSettings:
    keyword_mappings: list[KeywordMapping]
    auto_switch_scope: str
    keyword_switch_enabled: bool
    manage_wait_timeout: int
    admin_commands: dict[str, bool]
    auto_switch_announce: bool
    clear_context_on_switch: bool
    enable_llm_tools: bool


def load_settings(config: AstrBotConfig | None) -> PersonaPlusSettings:
    loaded: list[KeywordMapping] = []

    if not config:
        logger.warning("Persona+ 未载入专用配置，将使用默认值。")
        return PersonaPlusSettings(
            keyword_mappings=[],
            auto_switch_scope="conversation",
            keyword_switch_enabled=True,
            manage_wait_timeout=60,
            admin_commands={
                "switch": False,
                "create": True,
                "update": True,
                "delete": True,
                "view": False,
                "avatar": True,
                "list": False,
                "help": False,
            },
            auto_switch_announce=True,
            clear_context_on_switch=False,
            enable_llm_tools=False,
        )

    mappings_raw = config.get("keyword_mappings", [])

    if mappings_raw is None:
        entries: list[str | dict[str, str]] = []
    elif isinstance(mappings_raw, list):
        entries = mappings_raw
    elif isinstance(mappings_raw, str):
        entries = mappings_raw.splitlines()
    else:
        logger.warning(
            "Persona+ 关键词配置应为列表或文本，实际收到 %r (类型 %s)，尝试转换",
            mappings_raw,
            type(mappings_raw).__name__,
        )
        entries = [str(mappings_raw)] if str(mappings_raw).strip() else []

    for raw_entry in entries:
        if isinstance(raw_entry, dict):
            keyword = str(raw_entry.get("keyword", "")).strip()
            persona_id = str(raw_entry.get("persona_id", "")).strip()
            reply_template = str(raw_entry.get("reply_template", "")).strip()
            if keyword and persona_id:
                loaded.append(
                    KeywordMapping(
                        keyword=keyword,
                        persona_id=persona_id,
                        reply_template=reply_template,
                    )
                )
            continue

        entry = str(raw_entry).strip()
        if not entry or entry.startswith("#"):
            continue
        try:
            loaded.append(parse_mapping_entry(entry))
        except Exception as exc:  # noqa: BLE001
            logger.error("Persona+ 解析关键词配置失败: %s", exc)

    keyword_mappings = [m for m in loaded if m.keyword and m.persona_id]

    auto_switch_scope = config.get("auto_switch_scope", "conversation")
    keyword_switch_enabled = bool(config.get("enable_keyword_switching", True))

    admin_commands_raw = config.get(
        "admin_commands",
        {
            "switch": False,
            "create": True,
            "update": True,
            "delete": True,
            "view": False,
            "avatar": True,
            "list": False,
            "help": False,
        },
    )
    if isinstance(admin_commands_raw, dict):
        admin_commands = {
            str(command).lower().strip(): bool(required)
            for command, required in admin_commands_raw.items()
            if str(command).strip()
        }
    elif isinstance(admin_commands_raw, list):
        admin_commands = {
            str(cmd).lower().strip(): True
            for cmd in admin_commands_raw
            if str(cmd).strip()
        }
    else:
        logger.warning(
            "Persona+ admin_commands 配置应为字典，实际收到 %r，已使用默认值",
            admin_commands_raw,
        )
        admin_commands = {
            "switch": False,
            "create": True,
            "update": True,
            "delete": True,
            "view": False,
            "avatar": True,
            "list": False,
            "help": False,
        }

    auto_switch_announce = bool(config.get("enable_auto_switch_announce", True))
    clear_context_on_switch = bool(config.get("clear_context_on_switch", False))
    enable_llm_tools = bool(config.get("enable_llm_tools", False))

    raw_timeout = config.get("manage_wait_timeout_seconds", 60)
    try:
        timeout = int(raw_timeout)
    except (TypeError, ValueError):
        logger.warning(
            "Persona+ manage_wait_timeout_seconds=%r 非法，使用默认值 60",
            raw_timeout,
        )
        timeout = 60
    if timeout <= 0:
        logger.warning(
            "Persona+ manage_wait_timeout_seconds=%r 必须为正数，已重置为 60",
            raw_timeout,
        )
        timeout = 60

    return PersonaPlusSettings(
        keyword_mappings=keyword_mappings,
        auto_switch_scope=auto_switch_scope,
        keyword_switch_enabled=keyword_switch_enabled,
        manage_wait_timeout=timeout,
        admin_commands=admin_commands,
        auto_switch_announce=auto_switch_announce,
        clear_context_on_switch=clear_context_on_switch,
        enable_llm_tools=enable_llm_tools,
    )
