import asyncio
import gc
import importlib.util
import json
import sys
import time
import types
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import AsyncMock


class _Logger:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


def _decorator(*_args, **_kwargs):
    return lambda func: func


def _load_plugin_module():
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    star = types.ModuleType("astrbot.api.star")
    event = types.ModuleType("astrbot.api.event")
    provider_api = types.ModuleType("astrbot.api.provider")
    components = types.ModuleType("astrbot.api.message_components")

    class Star:
        def __init__(self, context):
            self.context = context

    class Context:
        pass

    class AstrMessageEvent:
        pass

    class Provider:
        pass

    class Plain:
        def __init__(self, text=""):
            self.text = text

    class At:
        def __init__(self, qq="", name=""):
            self.qq = qq
            self.name = name

    class AtAll(At):
        def __init__(self, name="全体成员"):
            super().__init__(qq="all", name=name)

    class Reply:
        def __init__(
            self,
            id="",
            sender_id="",
            sender_nickname="",
            message_str="",
            chain=None,
        ):
            self.id = id
            self.sender_id = sender_id
            self.sender_nickname = sender_nickname
            self.message_str = message_str
            self.chain = chain or []

    class Image:
        def __init__(self, file=""):
            self.file = file

    class Json:
        def __init__(self, data=None):
            self.data = data or {}

    class MessageChain:
        def __init__(self, chain=None):
            self.chain = list(chain) if chain else []

        def message(self, text):
            self.chain.append(Plain(text))
            return self

    filter_api = types.SimpleNamespace(
        event_message_type=_decorator,
        after_message_sent=_decorator,
        on_llm_request=_decorator,
        on_llm_response=_decorator,
        permission_type=_decorator,
        command=_decorator,
        EventMessageType=types.SimpleNamespace(GROUP_MESSAGE="group"),
        PermissionType=types.SimpleNamespace(ADMIN="admin"),
    )

    star.Star = Star
    star.Context = Context
    event.AstrMessageEvent = AstrMessageEvent
    event.MessageChain = MessageChain
    event.filter = filter_api
    provider_api.Provider = Provider
    components.Plain = Plain
    components.At = At
    components.AtAll = AtAll
    components.Reply = Reply
    components.Image = Image
    components.Json = Json
    api.star = star
    api.logger = _Logger()
    astrbot.api = api

    modules = {
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.star": star,
        "astrbot.api.event": event,
        "astrbot.api.provider": provider_api,
        "astrbot.api.message_components": components,
    }
    previous = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    try:
        module_path = Path(__file__).resolve().parents[1] / "main.py"
        spec = importlib.util.spec_from_file_location(
            "heartflow_test_module", module_path
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module, components
    finally:
        for name, old_module in previous.items():
            if old_module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old_module


heartflow, comp = _load_plugin_module()


class _Context:
    def __init__(self, judge_provider=None, main_provider=None):
        self.judge_provider = judge_provider
        self.main_provider = main_provider
        self.sent_messages = []

    def get_provider_by_id(self, _provider_id):
        return self.judge_provider

    async def get_using_provider_async(self, _umo):
        return self.main_provider

    async def send_message(self, umo, chain):
        self.sent_messages.append((umo, chain))
        return True


class _Event:
    def __init__(
        self,
        message="hello",
        *,
        wake=False,
        command=False,
        umo=None,
        sender_id="user-1",
        sender_name="alice",
        self_id="bot-1",
        message_id="msg-1",
        components=None,
    ):
        self.unified_msg_origin = umo or "test:GroupMessage:10001"
        self.message_str = message
        self.is_at_or_wake_command = wake
        self._sender_id = sender_id
        self._sender_name = sender_name
        self._self_id = self_id
        self.message_obj = types.SimpleNamespace(
            message_id=message_id,
            message=list(components) if components is not None else ([comp.Plain(message)] if message else []),
        )
        self._extras = {"handlers_parsed_params": {"command": {}} if command else {}}

    def get_messages(self):
        return self.message_obj.message

    def get_sender_name(self):
        return self._sender_name

    def get_sender_id(self):
        return self._sender_id

    def get_self_id(self):
        return self._self_id

    def get_extra(self, key, default=None):
        return self._extras.get(key, default)

    def set_extra(self, key, value):
        self._extras[key] = value

    def is_private_chat(self):
        return False

    def plain_result(self, text):
        return text

    def set_result(self, result):
        self.result = result


def _config(**overrides):
    config = {
        "enable_heartflow": True,
        "judge_provider_name": "judge",
        "judge_max_retries": 1,
        "context_messages_count": 5,
        "judge_context_count": 5,
        "look_interval_seconds": 900,
        "look_unread_threshold": 20,
        "look_debounce_seconds": 0,
        "min_reply_interval_seconds": 0,
        "judge_cooldown_seconds": 0,
        "judge_max_requests_per_minute": 0,
    }
    config.update(overrides)
    return config


class _Response:
    def __init__(self, completion_text, *, role="assistant", result_chain=None):
        self.completion_text = completion_text
        self.role = role
        self.result_chain = result_chain


class HeartflowAddressingTests(unittest.IsolatedAsyncioTestCase):
    async def test_at_other_and_reply_other_are_marked_directed(self):
        plugin = heartflow.HeartflowPlugin(_Context(), _config())
        try:
            e1 = _Event(
                "晚上开黑吗",
                sender_id="u1",
                sender_name="张三",
                components=[comp.At(qq="u2", name="李四"), comp.Plain("晚上开黑吗")],
            )
            e2 = _Event(
                "好啊九点",
                sender_id="u2",
                sender_name="李四",
                components=[
                    comp.Reply(id="m1", sender_id="u1", sender_nickname="张三", message_str="晚上开黑吗"),
                    comp.Plain("好啊九点"),
                ],
            )
            await plugin.on_group_message(e1)
            await plugin.on_group_message(e2)

            buf = plugin._get_raw_buffer(e1.unified_msg_origin)
            self.assertEqual(buf[0].target_type, "at_other")
            self.assertTrue(buf[0].skip_interject)
            self.assertEqual(buf[1].target_type, "reply_other")
            self.assertTrue(buf[1].skip_interject)
            self.assertFalse(plugin._batch_has_interjectable_candidate(buf))
        finally:
            await plugin.terminate()

    async def test_one_on_one_flow_without_at_is_detected(self):
        plugin = heartflow.HeartflowPlugin(_Context(), _config())
        try:
            e1 = _Event(
                "你作业交了吗",
                sender_id="u1",
                sender_name="张三",
                components=[comp.At(qq="u2", name="李四"), comp.Plain("你作业交了吗")],
            )
            e2 = _Event("交完了刚刚传上去", sender_id="u2", sender_name="李四")
            e3 = _Event("我还没写完呢", sender_id="u1", sender_name="张三")

            await plugin.on_group_message(e1)
            await plugin.on_group_message(e2)
            await plugin.on_group_message(e3)

            buf = plugin._get_raw_buffer(e1.unified_msg_origin)
            self.assertEqual(buf[1].target_type, "reply_in_flow")
            self.assertEqual(buf[1].target_id, "u1")
            self.assertEqual(buf[2].target_type, "reply_in_flow")
            self.assertEqual(buf[2].target_id, "u2")
            self.assertFalse(plugin._batch_has_interjectable_candidate(buf))
        finally:
            await plugin.terminate()

    async def test_group_callout_breaks_flow_and_becomes_interjectable(self):
        plugin = heartflow.HeartflowPlugin(_Context(), _config())
        try:
            e1 = _Event(
                "你作业交了吗",
                sender_id="u1",
                sender_name="张三",
                components=[comp.At(qq="u2", name="李四"), comp.Plain("你作业交了吗")],
            )
            e2 = _Event("大家觉得这周实验难吗", sender_id="u2", sender_name="李四")

            await plugin.on_group_message(e1)
            await plugin.on_group_message(e2)

            buf = plugin._get_raw_buffer(e1.unified_msg_origin)
            self.assertEqual(buf[1].target_type, "to_group")
            self.assertFalse(buf[1].skip_interject)
            self.assertTrue(plugin._batch_has_interjectable_candidate(buf))
        finally:
            await plugin.terminate()

    async def test_directed_only_batch_skips_judge_llm_and_advances_watermark(self):
        judge = AsyncMock()
        plugin = heartflow.HeartflowPlugin(_Context(), _config())
        plugin.judge_unread_batch = judge
        try:
            umo = "test:GroupMessage:directed"
            e1 = _Event(
                "你到家了吗",
                umo=umo,
                sender_id="u1",
                sender_name="张三",
                components=[comp.At(qq="u2", name="李四"), comp.Plain("你到家了吗")],
            )
            e2 = _Event("刚进门", umo=umo, sender_id="u2", sender_name="李四")
            await plugin.on_group_message(e1)
            await plugin.on_group_message(e2)

            await plugin._run_look_cycle(umo)

            judge.assert_not_awaited()
            state = plugin._get_chat_state(umo)
            self.assertEqual(state.skipped_directed_batches, 1)
            self.assertEqual(plugin._get_unread_messages(umo), [])
        finally:
            await plugin.terminate()


class HeartflowLookRhythmAndReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_look_interval_and_unread_threshold_calculation(self):
        plugin = heartflow.HeartflowPlugin(
            _Context(),
            _config(
                look_interval_seconds=900,
                look_unread_threshold=3,
                look_debounce_seconds=5,
            ),
        )
        try:
            umo = "test:GroupMessage:rhythm"
            now = time.time()
            state = plugin._get_chat_state(umo)
            state.last_look_time = now - 10

            # 1条未读 -> 需要等 900s 间隔
            plugin._raw_msg_buffer[umo] = deque(
                [heartflow.RawMessage("a", "u1", "m1", now - 2)], maxlen=50
            )
            wait1 = plugin._compute_next_wait_seconds(umo, now=now)
            self.assertGreater(wait1, 800)

            # 攒够3条未读 -> 不必等 900s，只需等防抖剩下的 3s
            plugin._raw_msg_buffer[umo].extend(
                [
                    heartflow.RawMessage("b", "u2", "m2", now - 2),
                    heartflow.RawMessage("c", "u3", "m3", now - 2),
                ]
            )
            wait2 = plugin._compute_next_wait_seconds(umo, now=now)
            self.assertAlmostEqual(wait2, 3.0, places=1)
        finally:
            await plugin.terminate()

    async def test_batch_look_picks_focus_topic_and_sends_proactive_reply(self):
        class JudgeProvider(heartflow.Provider):
            def __init__(self):
                self.calls = []

            async def text_chat(self, **kwargs):
                self.calls.append(kwargs)
                return _Response(
                    json.dumps(
                        {
                            "relevance": 9,
                            "willingness": 8,
                            "social": 9,
                            "timing": 9,
                            "continuity": 8,
                            "focus_index": 1,
                            "topic_summary": "周末去哪家拉面馆",
                            "should_quote": False,
                            "reasoning": "公开讨论吃的，适合顺嘴推荐",
                        },
                        ensure_ascii=False,
                    )
                )

        class MainProvider(heartflow.Provider):
            def __init__(self):
                self.calls = []

            async def text_chat(self, **kwargs):
                self.calls.append(kwargs)
                return _Response("街角那家豚骨的溏心蛋超赞，可以试试！")

        judge_prov = JudgeProvider()
        main_prov = MainProvider()
        ctx = _Context(judge_provider=judge_prov, main_provider=main_prov)
        plugin = heartflow.HeartflowPlugin(ctx, _config())
        try:
            umo = "test:GroupMessage:batch"
            await plugin.on_group_message(
                _Event(
                    "你到没",
                    umo=umo,
                    sender_id="u1",
                    sender_name="张三",
                    message_id="m0",
                    components=[comp.At(qq="u2", name="李四"), comp.Plain("你到没")],
                )
            )
            await plugin.on_group_message(
                _Event(
                    "周末大家想去吃哪家拉面呀",
                    umo=umo,
                    sender_id="u3",
                    sender_name="王五",
                    message_id="m1",
                )
            )
            await plugin.on_group_message(
                _Event("好饿", umo=umo, sender_id="u4", sender_name="赵六", message_id="m2")
            )

            await plugin._run_look_cycle(umo)

            self.assertEqual(len(judge_prov.calls), 1)
            self.assertEqual(len(main_prov.calls), 1)
            self.assertEqual(len(ctx.sent_messages), 1)
            sent_umo, sent_chain = ctx.sent_messages[0]
            self.assertEqual(sent_umo, umo)
            self.assertEqual(
                sent_chain.chain[-1].text,
                "街角那家豚骨的溏心蛋超赞，可以试试！",
            )
            state = plugin._get_chat_state(umo)
            self.assertEqual(state.total_replies, 1)
            self.assertLess(state.energy, 1.0)
            self.assertEqual(plugin._get_unread_messages(umo), [])
            # Bot 回复也记录到了缓冲区
            self.assertTrue(plugin._get_raw_buffer(umo)[-1].is_bot)
        finally:
            await plugin.terminate()

    async def test_quotes_focus_when_topic_buried_by_later_messages(self):
        class JudgeProvider(heartflow.Provider):
            async def text_chat(self, **_kwargs):
                return _Response(
                    json.dumps(
                        {
                            "relevance": 9,
                            "willingness": 9,
                            "social": 9,
                            "timing": 9,
                            "continuity": 8,
                            "focus_index": 0,
                            "topic_summary": "老话题",
                            "should_quote": True,
                        }
                    )
                )

        class MainProvider(heartflow.Provider):
            async def text_chat(self, **_kwargs):
                return _Response("我也觉得那个不错")

        ctx = _Context(judge_provider=JudgeProvider(), main_provider=MainProvider())
        plugin = heartflow.HeartflowPlugin(ctx, _config())
        try:
            umo = "test:GroupMessage:quote"
            for i in range(4):
                await plugin.on_group_message(
                    _Event(
                        f"msg-{i}",
                        umo=umo,
                        sender_id=f"u{i}",
                        sender_name=f"user{i}",
                        message_id=f"id-{i}",
                    )
                )

            await plugin._run_look_cycle(umo)

            self.assertEqual(len(ctx.sent_messages), 1)
            chain = ctx.sent_messages[0][1].chain
            self.assertIsInstance(chain[0], comp.Reply)
            self.assertEqual(chain[0].id, "id-0")
        finally:
            await plugin.terminate()

    async def test_judge_selecting_directed_message_as_focus_is_blocked(self):
        class JudgeProvider(heartflow.Provider):
            async def text_chat(self, **_kwargs):
                return _Response(
                    json.dumps(
                        {
                            "relevance": 9,
                            "willingness": 9,
                            "social": 9,
                            "timing": 9,
                            "continuity": 9,
                            "focus_index": 0,  # 指向 @他人 的那条
                            "topic_summary": "打断别人",
                        }
                    )
                )

        class MainProvider(heartflow.Provider):
            async def text_chat(self, **_kwargs):
                raise AssertionError("不应调用主模型")

        ctx = _Context(judge_provider=JudgeProvider(), main_provider=MainProvider())
        plugin = heartflow.HeartflowPlugin(ctx, _config())
        try:
            umo = "test:GroupMessage:blockfocus"
            await plugin.on_group_message(
                _Event(
                    "明天几点碰头",
                    umo=umo,
                    sender_id="u1",
                    sender_name="张三",
                    components=[comp.At(qq="u2", name="李四"), comp.Plain("明天几点碰头")],
                )
            )
            await plugin.on_group_message(
                _Event("今天天气真好", umo=umo, sender_id="u3", sender_name="王五")
            )

            await plugin._run_look_cycle(umo)
            self.assertEqual(ctx.sent_messages, [])
        finally:
            await plugin.terminate()

    async def test_main_model_skip_token_cancels_send(self):
        class JudgeProvider(heartflow.Provider):
            async def text_chat(self, **_kwargs):
                return _Response(
                    json.dumps(
                        {
                            "relevance": 8,
                            "willingness": 8,
                            "social": 8,
                            "timing": 8,
                            "continuity": 8,
                            "focus_index": 0,
                        }
                    )
                )

        class MainProvider(heartflow.Provider):
            async def text_chat(self, **_kwargs):
                return _Response("[SKIP]")

        ctx = _Context(judge_provider=JudgeProvider(), main_provider=MainProvider())
        plugin = heartflow.HeartflowPlugin(ctx, _config())
        try:
            umo = "test:GroupMessage:skip"
            await plugin.on_group_message(_Event("随便聊聊", umo=umo))
            await plugin._run_look_cycle(umo)
            self.assertEqual(ctx.sent_messages, [])
            self.assertEqual(plugin._get_chat_state(umo).total_replies, 0)
        finally:
            await plugin.terminate()

    async def test_self_share_limit_prevents_spamming(self):
        judge = AsyncMock()
        plugin = heartflow.HeartflowPlugin(
            _Context(), _config(self_share_limit=0.25)
        )
        plugin.judge_unread_batch = judge
        try:
            umo = "test:GroupMessage:share"
            now = time.time()
            plugin._raw_msg_buffer[umo] = deque(
                [
                    heartflow.RawMessage("bot", "bot", "b1", now - 20, is_bot=True),
                    heartflow.RawMessage("u1", "u1", "m1", now - 10, is_bot=False),
                    heartflow.RawMessage("u2", "u2", "m2", now - 5, is_bot=False),
                ],
                maxlen=50,
            )
            # 1 bot turn / (1 + 2) = 0.33 >= 0.25
            await plugin._run_look_cycle(umo)
            judge.assert_not_awaited()
            self.assertEqual(plugin._get_unread_messages(umo), [])
        finally:
            await plugin.terminate()

    async def test_wake_message_records_context_and_advances_watermark(self):
        plugin = heartflow.HeartflowPlugin(_Context(), _config())
        try:
            wake_event = _Event("@bot question", wake=True)
            command_event = _Event("/help", wake=True, command=True)

            await plugin.on_group_message(wake_event)
            await plugin.on_group_message(command_event)

            messages = plugin._get_raw_buffer(wake_event.unified_msg_origin)
            self.assertEqual([m.content for m in messages], ["@bot question"])
            self.assertEqual(
                plugin._get_unread_messages(wake_event.unified_msg_origin), []
            )
        finally:
            await plugin.terminate()


class HeartflowStateAndRateLimitTests(unittest.IsolatedAsyncioTestCase):
    def test_zero_weights_fall_back_to_defaults(self):
        plugin = heartflow.HeartflowPlugin(
            _Context(),
            _config(
                judge_relevance=0,
                judge_willingness=0,
                judge_social=0,
                judge_timing=0,
                judge_continuity=0,
            ),
        )
        self.assertAlmostEqual(sum(plugin.weights.values()), 1.0)
        self.assertEqual(plugin.weights["relevance"], 0.30)

    def test_chat_state_count_is_bounded(self):
        plugin = heartflow.HeartflowPlugin(_Context(), _config(max_tracked_chats=2))
        plugin._get_chat_state("group-1")
        plugin._get_chat_state("group-2")
        plugin._get_chat_state("group-3")
        self.assertEqual(len(plugin.chat_states), 2)
        self.assertNotIn("group-1", plugin.chat_states)

    def test_unused_chat_locks_are_weakly_referenced(self):
        plugin = heartflow.HeartflowPlugin(_Context(), _config())
        lock = plugin._get_chat_lock("temporary-group")
        self.assertIn("temporary-group", plugin._chat_locks)
        del lock
        gc.collect()
        self.assertNotIn("temporary-group", plugin._chat_locks)

    async def test_global_minute_limit_caps_total_requests(self):
        class JudgeProvider(heartflow.Provider):
            def __init__(self):
                self.calls = 0

            async def text_chat(self, **_kwargs):
                self.calls += 1
                return _Response(
                    '{"relevance":2,"willingness":2,"social":2,"timing":2,"continuity":2}'
                )

        prov = JudgeProvider()
        plugin = heartflow.HeartflowPlugin(
            _Context(judge_provider=prov),
            _config(judge_max_requests_per_minute=2),
        )
        try:
            for index in range(4):
                umo = f"group-{index}"
                await plugin.on_group_message(_Event("hi", umo=umo))
                await plugin._run_look_cycle(umo)
            self.assertEqual(prov.calls, 2)
            self.assertEqual(plugin._recent_judge_count(), 2)
        finally:
            await plugin.terminate()

    async def test_judge_retries_and_timeout_safety(self):
        class SlowProvider(heartflow.Provider):
            async def text_chat(self, **_kwargs):
                await asyncio.sleep(1)
                return _Response("{}")

        plugin = heartflow.HeartflowPlugin(
            _Context(judge_provider=SlowProvider()), _config()
        )
        plugin.judge_timeout_seconds = 0.02
        try:
            umo = "test:GroupMessage:timeout"
            await plugin.on_group_message(_Event("hello", umo=umo))
            await plugin._run_look_cycle(umo)
            self.assertFalse(plugin._get_chat_lock(umo).locked())
        finally:
            await plugin.terminate()

    async def test_status_command_reports_look_rhythm(self):
        plugin = heartflow.HeartflowPlugin(
            _Context(),
            _config(
                look_interval_seconds=900,
                look_unread_threshold=20,
                judge_cooldown_seconds=60,
                judge_max_requests_per_minute=12,
            ),
        )
        try:
            event = _Event("/heartflow")
            await plugin.heartflow_status(event)
            self.assertIn("900s", event.result)
            self.assertIn("20条", event.result)
            self.assertIn("60s/群", event.result)
            self.assertIn("12次/分钟", event.result)
        finally:
            await plugin.terminate()


if __name__ == "__main__":
    unittest.main()
