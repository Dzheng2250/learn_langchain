import json
import unittest
from types import SimpleNamespace

from langchain_core.messages import (
    AIMessage,
    SystemMessage,
    ToolMessage,
    messages_to_dict,
)

from src.core.session.history import SessionHistoryQueryService
from src.core.history_models import ConversationHistoryPage, HistoryMessageRecord


class LifecycleStore:
    def __init__(self, existing):
        self.existing = existing

    def resolve_workspace(self, root):
        return SimpleNamespace(root=root)

    def find_session(self, _workspace, _name):
        return self.existing


class HistoryReader:
    def __init__(self, page):
        self.page = page
        self.calls = []

    def list_page(self, session, **kwargs):
        self.calls.append((session, kwargs))
        return self.page


def record(message_id, turn_index, ordinal, role, message):
    return HistoryMessageRecord(
        message_id=message_id,
        turn_index=turn_index,
        message_ordinal=ordinal,
        role=role,
        message_type=message.__class__.__name__,
        content=str(message.content),
        raw=messages_to_dict([message])[0],
    )


class SessionHistoryQueryServiceTest(unittest.TestCase):
    def test_missing_session_returns_empty_history_without_reading(self):
        reader = HistoryReader(ConversationHistoryPage((), None, False))
        service = SessionHistoryQueryService(
            lifecycle_store=LifecycleStore(None),
            history_reader=reader,
        )

        result = service.list_history(".", "missing")

        self.assertEqual([], result["turns"])
        self.assertEqual([], reader.calls)

    def test_history_normalizes_reasoning_tools_and_redacts_arguments(self):
        assistant = AIMessage(
            content=[
                {"type": "thinking", "thinking": "private reasoning"},
                {
                    "type": "tool_use",
                    "id": "tool-1",
                    "name": "write_workspace_file",
                    "input": {
                        "path": "result.txt",
                        "content": "secret body",
                        "api_key": "do-not-return",
                    },
                },
            ],
            tool_calls=[{
                "id": "tool-1",
                "name": "write_workspace_file",
                "args": {"path": "result.txt"},
                "type": "tool_call",
            }],
        )
        tool_result = ToolMessage(
            content="created result.txt",
            tool_call_id="tool-1",
            name="write_workspace_file",
        )
        page = ConversationHistoryPage(
            (
                record("a", 4, 1, "assistant", assistant),
                record("b", 4, 2, "tool", tool_result),
            ),
            4,
            True,
        )
        service = SessionHistoryQueryService(
            lifecycle_store=LifecycleStore((SimpleNamespace(), False)),
            history_reader=HistoryReader(page),
            reasoning_display="collapsed",
        )

        result = service.list_history(".", "default", limit_turns=30)

        blocks = result["turns"][0]["messages"][0]["blocks"]
        self.assertEqual("private reasoning", blocks[0]["content"])
        self.assertEqual("<11 chars omitted>", blocks[1]["args"]["content"])
        self.assertEqual("<redacted>", blocks[1]["args"]["api_key"])
        self.assertNotIn("do-not-return", str(result))
        tool_block = result["turns"][0]["messages"][1]["blocks"][0]
        self.assertEqual("tool_result", tool_block["type"])
        self.assertEqual("created result.txt", tool_block["content"])
        self.assertEqual(4, result["next_before_turn"])
        self.assertTrue(result["has_more"])

    def test_metadata_reasoning_omits_raw_content(self):
        assistant = AIMessage(
            content=[{"type": "thinking", "thinking": "not exposed"}]
        )
        page = ConversationHistoryPage(
            (record("a", 1, 1, "assistant", assistant),),
            None,
            False,
        )
        service = SessionHistoryQueryService(
            lifecycle_store=LifecycleStore((SimpleNamespace(), True)),
            history_reader=HistoryReader(page),
            reasoning_display="metadata",
        )

        result = service.list_history(".", "archived")

        block = result["turns"][0]["messages"][0]["blocks"][0]
        self.assertEqual(11, block["char_count"])
        self.assertNotIn("content", block)
        self.assertTrue(result["archived"])

    def test_invalid_legacy_raw_uses_projected_content(self):
        page = ConversationHistoryPage(
            (
                HistoryMessageRecord(
                    message_id="legacy",
                    turn_index=1,
                    message_ordinal=1,
                    role="assistant",
                    message_type="AIMessage",
                    content="legacy answer",
                    raw={"unexpected": True},
                ),
            ),
            None,
            False,
        )
        service = SessionHistoryQueryService(
            lifecycle_store=LifecycleStore((SimpleNamespace(), False)),
            history_reader=HistoryReader(page),
        )

        result = service.list_history(".", "default")

        self.assertEqual(
            "legacy answer",
            result["turns"][0]["messages"][0]["blocks"][0]["text"],
        )

    def test_legacy_role_and_embedded_secret_are_normalized(self):
        assistant = AIMessage(
            content=[{
                "type": "tool_use",
                "id": "tool-1",
                "name": "run_command_in_container",
                "input": {
                    "command": "curl -H 'Authorization: bearer-secret' example.test",
                },
            }]
        )
        page = ConversationHistoryPage(
            (record("legacy-ai", 1, 1, "ai", assistant),),
            None,
            False,
        )
        service = SessionHistoryQueryService(
            lifecycle_store=LifecycleStore((SimpleNamespace(), False)),
            history_reader=HistoryReader(page),
            reasoning_display="collapsed",
        )

        result = service.list_history(".", "default")

        message = result["turns"][0]["messages"][0]
        self.assertEqual("assistant", message["role"])
        command = message["blocks"][0]["args"]["command"]
        self.assertNotIn("bearer-secret", command)
        self.assertIn("[REDACTED]", command)

    def test_system_messages_and_file_result_bodies_are_not_exposed(self):
        page = ConversationHistoryPage(
            (
                record("system", 1, 1, "system", SystemMessage(content="internal")),
                record(
                    "tool",
                    1,
                    2,
                    "tool",
                    ToolMessage(
                        content="complete file body",
                        tool_call_id="tool-1",
                        name="read_workspace_file",
                    ),
                ),
            ),
            None,
            False,
        )
        service = SessionHistoryQueryService(
            lifecycle_store=LifecycleStore((SimpleNamespace(), False)),
            history_reader=HistoryReader(page),
        )

        result = service.list_history(".", "default")

        messages = result["turns"][0]["messages"]
        self.assertEqual(["tool"], [message["role"] for message in messages])
        content = messages[0]["blocks"][0]["content"]
        self.assertNotIn("complete file body", content)
        self.assertIn("18 chars", content)

    def test_native_anthropic_tool_result_block_is_preserved_safely(self):
        page = ConversationHistoryPage(
            (
                HistoryMessageRecord(
                    message_id="native-tool-result",
                    turn_index=2,
                    message_ordinal=3,
                    role="user",
                    message_type="user",
                    content="",
                    raw={
                        "role": "user",
                        "content": [{
                            "type": "tool_result",
                            "tool_use_id": "tool-1",
                            "content": "result with token=hidden-value",
                            "is_error": True,
                        }],
                    },
                ),
            ),
            None,
            False,
        )
        service = SessionHistoryQueryService(
            lifecycle_store=LifecycleStore((SimpleNamespace(), False)),
            history_reader=HistoryReader(page),
        )

        result = service.list_history(".", "legacy-native")

        block = result["turns"][0]["messages"][0]["blocks"][0]
        self.assertEqual("tool_result", block["type"])
        self.assertEqual("tool-1", block["tool_call_id"])
        self.assertTrue(block["is_error"])
        self.assertNotIn("hidden-value", block["content"])

    def test_large_page_is_trimmed_only_at_complete_turn_boundaries(self):
        records = tuple(
            record(
                f"assistant-{turn_index}",
                turn_index,
                1,
                "assistant",
                AIMessage(content="x" * 40_000),
            )
            for turn_index in range(1, 31)
        )
        service = SessionHistoryQueryService(
            lifecycle_store=LifecycleStore((SimpleNamespace(), False)),
            history_reader=HistoryReader(
                ConversationHistoryPage(records, None, False)
            ),
        )

        result = service.list_history(".", "large", limit_turns=30)

        self.assertLess(len(result["turns"]), 30)
        self.assertTrue(result["has_more"])
        self.assertEqual(
            result["turns"][0]["turn_index"],
            result["next_before_turn"],
        )
        self.assertEqual(30, result["turns"][-1]["turn_index"])
        self.assertTrue(all(len(turn["messages"]) == 1 for turn in result["turns"]))
        encoded = json.dumps(result, ensure_ascii=False).encode("utf-8")
        self.assertLess(len(encoded), 800_000)

    def test_single_oversized_turn_is_projected_below_frame_budget(self):
        content = "界" * 400_000
        service = SessionHistoryQueryService(
            lifecycle_store=LifecycleStore((SimpleNamespace(), False)),
            history_reader=HistoryReader(
                ConversationHistoryPage(
                    (record("oversized", 7, 1, "assistant", AIMessage(content=content)),),
                    None,
                    False,
                )
            ),
        )

        result = service.list_history(".", "oversized", limit_turns=30)

        self.assertEqual(1, len(result["turns"]))
        turn = result["turns"][0]
        block = turn["messages"][0]["blocks"][0]
        self.assertTrue(turn["truncated"])
        self.assertTrue(block["truncated"])
        self.assertEqual(len(content), block["char_count"])
        self.assertEqual(len(content.encode("utf-8")), block["original_bytes"])
        self.assertTrue(block["text"].endswith("<History content truncated>"))
        self.assertLess(
            len(json.dumps(result, ensure_ascii=False).encode("utf-8")),
            800_000,
        )


if __name__ == "__main__":
    unittest.main()
