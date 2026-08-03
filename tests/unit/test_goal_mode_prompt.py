import unittest

from langchain_core.messages import AIMessage, HumanMessage

from src.core.prompts.goal_mode import (
    GOAL_COMPLETION_REVIEW,
    GOAL_MODE_INSTRUCTION,
    completion_review_message,
    contains_completion_review,
    inject_goal_mode_prompt,
    replace_current_user_prompt,
    sanitize_goal_messages,
)
from src.core.prompts.parent_agent import build_parent_system_prompt


class GoalModePromptTest(unittest.TestCase):
    def test_goal_policy_is_attached_after_the_user_prompt(self):
        prompt = inject_goal_mode_prompt("Refactor the service")

        self.assertTrue(prompt.startswith("Refactor the service\n\n"))
        self.assertIn(GOAL_MODE_INSTRUCTION, prompt)

    def test_parent_system_prompt_is_mode_independent(self):
        prompt = build_parent_system_prompt("skills", 200)

        self.assertNotIn("Goal mode is active", prompt)
        self.assertIn("Task planning tools are available", prompt)

    def test_model_prompt_replacement_marks_only_current_human_message(self):
        original = [HumanMessage(content="old"), HumanMessage(content="request")]

        updated = replace_current_user_prompt(original, "request\n\npolicy")

        self.assertEqual("old", updated[0].content)
        self.assertEqual("request\n\npolicy", updated[1].content)
        self.assertEqual(
            "goal_mode_prompt",
            updated[1].additional_kwargs["learn_agent_internal_prompt"],
        )

    def test_sanitizer_restores_original_input_and_removes_review_message(self):
        prepared = replace_current_user_prompt(
            [HumanMessage(content="request")],
            "request\n\npolicy",
        )
        messages = [
            *prepared,
            AIMessage(content="first answer"),
            completion_review_message(),
            AIMessage(content="final answer"),
        ]

        sanitized = sanitize_goal_messages(messages, "request")

        self.assertEqual(
            ["request", "first answer", "final answer"],
            [message.content for message in sanitized],
        )
        self.assertNotIn(
            "learn_agent_internal_prompt",
            sanitized[0].additional_kwargs,
        )

    def test_checkpoint_marker_prevents_duplicate_review(self):
        self.assertFalse(contains_completion_review([HumanMessage(content="request")]))
        self.assertTrue(contains_completion_review([completion_review_message()]))
    def test_completion_review_is_marked_synthetic(self):
        message = completion_review_message()

        self.assertEqual(GOAL_COMPLETION_REVIEW, message.content)
        self.assertEqual(
            "goal_completion_review",
            message.additional_kwargs["learn_agent_internal_prompt"],
        )


if __name__ == "__main__":
    unittest.main()
