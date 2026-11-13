"""End-to-end checks against a real AstrBot source installation.

Run this script through AstrBot's isolated uv environment. It uses AstrBot's
real event, context, provider, response, configuration, and message-chain
types while replacing only network-facing provider and platform operations.
"""

import argparse
import asyncio
import copy
import functools
import importlib.util
import json
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock


def _load_plugin(plugin_root: Path):
    module_path = plugin_root / "main.py"
    spec = importlib.util.spec_from_file_location("heartflow_astrbot_e2e", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load plugin module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def _run(plugin_root: Path) -> dict:
    from astrbot.core.config.astrbot_config import AstrBotConfig
    from astrbot.core.config.default import DEFAULT_CONFIG
    from astrbot.core.message.components import At, Plain, Reply
    from astrbot.core.pipeline.context_utils import call_event_hook, call_handler
    from astrbot.core.platform.astr_message_event import AstrMessageEvent
    from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
    from astrbot.core.platform.message_type import MessageType
    from astrbot.core.platform.platform_metadata import PlatformMetadata
    from astrbot.core.provider.entities import LLMResponse
    from astrbot.core.provider.provider import Provider
    from astrbot.core.star.context import Context
    from astrbot.core.star.star import star_map
    from astrbot.core.star.star_handler import EventType, star_handlers_registry
    from astrbot.core.star.star_manager import PluginManager

    metadata = PluginManager._load_plugin_metadata(str(plugin_root))
    assert metadata is not None
    compatible, error = PluginManager._validate_astrbot_version_specifier(
        metadata.astrbot_version
    )
    assert compatible, error

    schema = json.loads((plugin_root / "_conf_schema.json").read_text(encoding="utf-8"))

    class DeterministicProvider(Provider):
        def __init__(self, provider_id: str, *, mode: str) -> None:
            super().__init__(
                {"id": provider_id, "type": "heartflow_e2e", "model": "deterministic"},
                {},
            )
            self.mode = mode
            self.calls = []
            self.delay = 0.0

        def get_current_key(self) -> str:
            return "e2e"

        def set_key(self, key: str) -> None:
            del key

        async def get_models(self) -> list[str]:
            return ["deterministic"]

        async def text_chat(self, **kwargs) -> LLMResponse:
            self.calls.append(kwargs)
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.mode == "judge":
                payload = {
                    "relevance": 9,
                    "willingness": 9,
                    "social": 9,
                    "timing": 9,
                    "continuity": 8,
                    "focus_index": 1,
                    "topic_summary": "group discussion",
                    "should_quote": False,
                    "reasoning": "deterministic e2e score",
                }
                return LLMResponse(
                    role="assistant",
                    completion_text=json.dumps(payload, ensure_ascii=False),
                )
            return LLMResponse(
                role="assistant",
                completion_text="A natural proactive reply.",
            )

    class RecordingEvent(AstrMessageEvent):
        async def send(self, message) -> None:
            del message

        async def send_streaming(self, generator, use_fallback: bool = False) -> None:
            del generator, use_fallback

    class ConversationManager:
        async def get_curr_conversation_id(self, _umo):
            return None

    class PersonaManager:
        async def get_default_persona_v3(self, _umo):
            return {"prompt": "A friendly group-chat bot."}

    def make_event(
        session_id: str,
        text: str,
        *,
        wake: bool = False,
        sender_id: str = "user-1",
        sender_name: str = "Alice",
        components=None,
    ):
        message = AstrBotMessage()
        message.type = MessageType.GROUP_MESSAGE
        message.self_id = "bot-1"
        message.session_id = session_id
        message.message_id = f"message-{session_id}-{time.monotonic_ns()}"
        message.group_id = session_id
        message.sender = MessageMember(user_id=sender_id, nickname=sender_name)
        message.message = components if components is not None else [Plain(text)]
        message.message_str = text
        message.raw_message = None
        event = RecordingEvent(
            message_str=text,
            message_obj=message,
            platform_meta=PlatformMetadata(
                name="heartflow_e2e",
                description="Heartflow integration platform",
                id="heartflow_e2e",
                support_streaming_message=True,
            ),
            session_id=session_id,
        )
        event.is_at_or_wake_command = wake
        event.is_wake = wake
        event.set_extra("handlers_parsed_params", {})
        return event

    with tempfile.TemporaryDirectory(prefix="heartflow_e2e_") as temp_dir:
        temp_path = Path(temp_dir)
        plugin_config = AstrBotConfig(
            config_path=str(temp_path / "heartflow.json"),
            schema=schema,
        )
        plugin_config.update(
            {
                "enable_heartflow": True,
                "judge_provider_name": "judge",
                "reply_threshold": 0.6,
                "look_interval_seconds": 60,
                "look_unread_threshold": 2,
                "look_debounce_seconds": 0,
                "judge_cooldown_seconds": 0,
                "judge_max_requests_per_minute": 0,
                "min_reply_interval_seconds": 0,
                "judge_max_retries": 1,
                "judge_timeout_seconds": 5,
            }
        )

        main_config = copy.deepcopy(DEFAULT_CONFIG)
        main_config["provider_ltm_settings"]["active_reply"]["enable"] = False
        judge_provider = DeterministicProvider("judge", mode="judge")
        main_provider = DeterministicProvider("main", mode="main")
        sent_messages = []

        provider_manager = SimpleNamespace(inst_map={"judge": judge_provider})
        config_manager = SimpleNamespace(get_conf=lambda _umo: main_config)
        context = Context(
            asyncio.Queue(),
            main_config,
            MagicMock(),
            provider_manager,
            MagicMock(),
            ConversationManager(),
            MagicMock(),
            PersonaManager(),
            config_manager,
            MagicMock(),
            MagicMock(),
        )
        context.get_using_provider_async = lambda _umo=None: asyncio.sleep(
            0, result=main_provider
        )

        async def _send_message(session, message_chain):
            sent_messages.append((session, message_chain))
            return True

        context.send_message = _send_message

        heartflow = _load_plugin(plugin_root)
        plugin = heartflow.HeartflowPlugin(context=context, config=plugin_config)

        module_handlers = star_handlers_registry.get_handlers_by_module_name(
            heartflow.__name__
        )
        handler_types = {handler.event_type for handler in module_handlers}
        assert EventType.AdapterMessageEvent in handler_types
        assert EventType.OnLLMResponseEvent in handler_types
        runtime_metadata = star_map[heartflow.__name__]
        runtime_metadata.name = metadata.name
        runtime_metadata.activated = True

        def bind_plugin(instance) -> None:
            runtime_metadata.star_cls = instance
            for handler in module_handlers:
                raw_handler = (
                    handler.handler.func
                    if isinstance(handler.handler, functools.partial)
                    else handler.handler
                )
                handler.handler = functools.partial(raw_handler, instance)

        async def dispatch_group_message(event) -> None:
            group_handler = next(
                handler
                for handler in module_handlers
                if handler.handler_name == "on_group_message"
            )
            assert all(
                event_filter.filter(event, main_config)
                for event_filter in group_handler.event_filters
            )
            async for _ in call_handler(event, group_handler.handler):
                pass

        bind_plugin(plugin)

        # 1. 定向他人对话流自动过滤（不调小模型）
        directed_1 = make_event(
            "directed-group",
            "晚上打游戏吗",
            sender_id="u1",
            sender_name="张三",
            components=[At(qq="u2", name="李四"), Plain("晚上打游戏吗")],
        )
        directed_2 = make_event(
            "directed-group",
            "来啊",
            sender_id="u2",
            sender_name="李四",
            components=[
                Reply(id="1", sender_id="u1", sender_nickname="张三", message_str="晚上打游戏吗"),
                Plain("来啊"),
            ],
        )
        await dispatch_group_message(directed_1)
        await dispatch_group_message(directed_2)
        await plugin._run_look_cycle(directed_1.unified_msg_origin)
        assert len(judge_provider.calls) == 0
        assert len(sent_messages) == 0

        # 2. 攒批看群 + 选中公开话题 + 主模型主动发送
        e1 = make_event("batch-group", "你到家没", sender_id="u1", sender_name="张三", components=[At(qq="u2", name="李四"), Plain("你到家没")])
        e2 = make_event("batch-group", "大家周末想去吃啥", sender_id="u3", sender_name="王五")
        await dispatch_group_message(e1)
        await dispatch_group_message(e2)
        await plugin._run_look_cycle(e1.unified_msg_origin)
        assert len(judge_provider.calls) == 1
        assert len(main_provider.calls) == 1
        assert len(sent_messages) == 1

        # 3. 正常唤醒回复也写入缓冲区
        wake_event = make_event("wake-group", "A direct wake message", wake=True)
        await dispatch_group_message(wake_event)
        await call_event_hook(
            wake_event,
            EventType.OnLLMResponseEvent,
            LLMResponse(role="assistant", completion_text="Wake reply text."),
        )
        assert plugin._get_last_bot_reply(wake_event.unified_msg_origin) == "Wake reply text."

        await plugin.terminate()

    return {
        "astrbot_version_compatible": True,
        "schema_loaded_by_astrbot": True,
        "real_handler_registration": True,
        "directed_messages_filtered": True,
        "batch_look_and_send": True,
        "wake_response_recorded": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-root", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(_run(args.plugin_root.resolve()))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
