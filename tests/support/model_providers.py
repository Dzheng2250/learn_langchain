"""Model-provider fakes shared by tests that must not call an LLM."""


class UnusedModelProvider:
    """Fail immediately if a test unexpectedly reaches model construction."""

    def create_chat_model(self, *_args, **_kwargs):
        raise AssertionError("This test path must not create or call an LLM.")
