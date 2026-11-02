from chinuuu.assistant import Assistant
from chinuuu.planner import ToolExecutionPolicy
from chinuuu.tools import ToolRegistry


class FakeClient:
    def __init__(self):
        self.responses = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "calculate", "arguments": {"expression": "20 * 3"}}}
                ],
            },
            {"role": "assistant", "content": "The result is 60."},
        ]
        self.seen_messages = []

    def chat_message(self, messages, tools=None):
        self.seen_messages.append(messages.copy())
        return self.responses.pop(0)


def test_agent_loop_executes_registered_tool_and_returns_final_answer(tmp_path):
    assistant = Assistant.__new__(Assistant)
    assistant.policy = ToolExecutionPolicy(max_tool_rounds=2, max_tool_calls=4)
    assistant.client = FakeClient()
    registry = ToolRegistry(tmp_path / "workspace")

    result = assistant._agent_loop(
        [{"role": "user", "content": "Calculate 20 times 3"}], registry, None
    )

    assert result == "The result is 60."
    tool_messages = [m for m in assistant.client.seen_messages[1] if m.get("role") == "tool"]
    assert tool_messages and tool_messages[0]["content"] == "60"


def test_tool_round_limit_stops_extra_tool_calls(tmp_path):
    assistant = Assistant.__new__(Assistant)
    assistant.policy = ToolExecutionPolicy(max_tool_rounds=1, max_tool_calls=8)

    class RepeatingClient:
        def __init__(self):
            self.calls = 0

        def chat_message(self, messages, tools=None):
            self.calls += 1
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "write_workspace_file",
                            "arguments": {"path": f"file{self.calls}.txt", "content": "x"},
                        }
                    }
                ],
            }

    assistant.client = RepeatingClient()
    workspace = tmp_path / "workspace"
    registry = ToolRegistry(workspace)
    result = assistant._agent_loop(
        [{"role": "user", "content": "Create a file"}],
        registry,
        lambda _name, _args: True,
        user_text="Create a file",
    )

    assert "specify the exact file name" in result
    assert not (workspace / "file1.txt").exists()
    assert not (workspace / "file2.txt").exists()


def test_simple_file_creation_returns_without_second_llm_round(tmp_path):
    class SlowSecondRoundClient:
        def __init__(self):
            self.calls = 0

        def chat_message(self, messages, tools=None):
            self.calls += 1
            if self.calls == 1:
                return {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "function": {
                            "name": "write_workspace_file",
                            "arguments": {"path": "hello.txt", "content": "Hello from Chinuuu", "overwrite": True},
                        }
                    }],
                }
            raise AssertionError("A second model inference must be skipped for simple file creation")

    assistant = Assistant.__new__(Assistant)
    assistant.policy = ToolExecutionPolicy(max_tool_rounds=2, max_tool_calls=4)
    assistant.client = SlowSecondRoundClient()
    workspace = tmp_path / "workspace"
    registry = ToolRegistry(workspace)

    result = assistant._agent_loop(
        [{"role": "user", "content": "Yes, create hello.txt"}],
        registry,
        confirm=None,
        user_text="Yes, create hello.txt",
    )

    assert "Created file:" in result
    assert (workspace / "hello.txt").read_text(encoding="utf-8") == "Hello from Chinuuu"
    assert assistant.client.calls == 1


def test_simple_directory_creation_returns_without_second_llm_round(tmp_path):
    class OneCallClient:
        def __init__(self):
            self.calls = 0

        def chat_message(self, messages, tools=None):
            self.calls += 1
            if self.calls == 1:
                return {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "function": {
                            "name": "create_workspace_directory",
                            "arguments": {"path": "notes"},
                        }
                    }],
                }
            raise AssertionError("Unexpected extra model round for a simple directory creation")

    assistant = Assistant.__new__(Assistant)
    assistant.policy = ToolExecutionPolicy(max_tool_rounds=2, max_tool_calls=4)
    assistant.client = OneCallClient()
    workspace = tmp_path / "workspace"
    registry = ToolRegistry(workspace)

    result = assistant._agent_loop(
        [{"role": "user", "content": "Create folder notes"}],
        registry,
        confirm=None,
        user_text="Create folder notes",
    )

    assert "Created directory:" in result
    assert (workspace / "notes").is_dir()
    assert assistant.client.calls == 1


def test_underscored_folder_name_returns_verified_path_without_second_model_call(tmp_path):
    class SingleCallClient:
        def __init__(self):
            self.calls = 0

        def chat_message(self, messages, tools=None):
            self.calls += 1
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "create_workspace_directory",
                        "arguments": {"path": "test_folder"},
                    }
                }],
            }

    assistant = Assistant.__new__(Assistant)
    assistant.policy = ToolExecutionPolicy(max_tool_rounds=2, max_tool_calls=4)
    assistant.client = SingleCallClient()
    workspace = tmp_path / "workspace"
    registry = ToolRegistry(workspace)
    result = assistant._agent_loop(
        [{"role": "user", "content": "Create test_folder"}],
        registry,
        confirm=None,
        user_text="Create test_folder",
    )
    assert str(workspace.resolve() / "test_folder") in result
    assert assistant.client.calls == 1
    assert (workspace / "test_folder").is_dir()


def test_model_cannot_invent_directory_name_not_in_operator_request(tmp_path):
    class InventingClient:
        def chat_message(self, messages, tools=None):
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "create_workspace_directory",
                        "arguments": {"path": "invented_folder"},
                    }
                }],
            }

    assistant = Assistant.__new__(Assistant)
    assistant.policy = ToolExecutionPolicy(max_tool_rounds=2, max_tool_calls=4)
    assistant.client = InventingClient()
    workspace = tmp_path / "workspace"
    registry = ToolRegistry(workspace)
    result = assistant._agent_loop(
        [{"role": "user", "content": "Create a folder"}],
        registry,
        confirm=None,
        user_text="Create a folder",
    )
    assert "exact directory name" in result
    assert not (workspace / "invented_folder").exists()


def test_file_creation_can_resolve_parent_folder_from_prior_user_message(tmp_path):
    class CreateFileClient:
        def __init__(self):
            self.calls = 0

        def chat_message(self, messages, tools=None):
            self.calls += 1
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "write_workspace_file",
                        "arguments": {
                            "path": "test_folder/first_program.py",
                            "content": "print('hello')\n",
                        },
                    }
                }],
            }

    assistant = Assistant.__new__(Assistant)
    assistant.policy = ToolExecutionPolicy(max_tool_rounds=2, max_tool_calls=4)
    assistant.client = CreateFileClient()
    workspace = tmp_path / "workspace"
    (workspace / "test_folder").mkdir(parents=True)
    registry = ToolRegistry(workspace)
    messages = [
        {"role": "user", "content": "Create test_folder"},
        {"role": "assistant", "content": "Created directory: test_folder"},
        {
            "role": "user",
            "content": "Create a file in that folder named first_program.py and write simple Python code.",
        },
    ]

    result = assistant._agent_loop(
        messages,
        registry,
        confirm=None,
        user_text="Create a file in that folder named first_program.py and write simple Python code.",
    )

    created = workspace / "test_folder" / "first_program.py"
    assert created.is_file()
    assert created.read_text(encoding="utf-8") == "print('hello')\n"
    assert "Filesystem verification: file exists and content matches" in result
    assert str(created.resolve()) in result
    assert assistant.client.calls == 1


def test_unmentioned_parent_folder_is_rejected_even_when_filename_is_explicit(tmp_path):
    class InventingParentClient:
        def chat_message(self, messages, tools=None):
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "write_workspace_file",
                        "arguments": {"path": "invented_folder/script.py", "content": "print('x')"},
                    }
                }],
            }

    assistant = Assistant.__new__(Assistant)
    assistant.policy = ToolExecutionPolicy(max_tool_rounds=2, max_tool_calls=4)
    assistant.client = InventingParentClient()
    workspace = tmp_path / "workspace"
    registry = ToolRegistry(workspace)
    result = assistant._agent_loop(
        [
            {"role": "user", "content": "Create a folder called approved_folder"},
            {"role": "user", "content": "Create a file named script.py in that folder"},
        ],
        registry,
        confirm=None,
        user_text="Create a file named script.py in that folder",
    )

    assert "specify the exact" in result
    assert not (workspace / "invented_folder").exists()
    assert not (workspace / "approved_folder" / "script.py").exists()


def test_failed_filesystem_mutation_stops_agent_loop_without_second_model_call(tmp_path):
    class FailedWriteClient:
        def __init__(self):
            self.calls = 0

        def chat_message(self, messages, tools=None):
            self.calls += 1
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "write_workspace_file",
                        "arguments": {"path": "existing.py", "content": "print('replacement')"},
                    }
                }],
            }

    assistant = Assistant.__new__(Assistant)
    assistant.policy = ToolExecutionPolicy(max_tool_rounds=2, max_tool_calls=4)
    assistant.client = FailedWriteClient()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    existing = workspace / "existing.py"
    existing.write_text("original", encoding="utf-8")
    registry = ToolRegistry(workspace)

    result = assistant._agent_loop(
        [{"role": "user", "content": "Create existing.py"}],
        registry,
        confirm=None,
        user_text="Create existing.py",
    )

    assert "Filesystem operation failed" in result
    assert "already exists" in result
    assert existing.read_text(encoding="utf-8") == "original"
    assert assistant.client.calls == 1


def test_agent_does_not_report_success_when_tool_claims_write_but_file_is_absent(tmp_path):
    class FalseSuccessRegistry(ToolRegistry):
        def execute(self, name, arguments, confirm=None):
            if name == "write_workspace_file":
                return "Created file: fake.py (12 bytes)."
            return super().execute(name, arguments, confirm)

    class FalseSuccessClient:
        def chat_message(self, messages, tools=None):
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "write_workspace_file",
                        "arguments": {"path": "fake.py", "content": "print('fake')"},
                    }
                }],
            }

    assistant = Assistant.__new__(Assistant)
    assistant.policy = ToolExecutionPolicy(max_tool_rounds=2, max_tool_calls=4)
    assistant.client = FalseSuccessClient()
    workspace = tmp_path / "workspace"
    registry = FalseSuccessRegistry(workspace)

    result = assistant._agent_loop(
        [{"role": "user", "content": "Create fake.py"}],
        registry,
        confirm=None,
        user_text="Create fake.py",
    )

    assert "could not be verified" in result
    assert "will not claim" in result
    assert not (workspace / "fake.py").exists()


def test_unknown_tool_failure_stops_loop_instead_of_being_paraphrased(tmp_path):
    class UnknownToolClient:
        def __init__(self):
            self.calls = 0

        def chat_message(self, messages, tools=None):
            self.calls += 1
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "delete_workspace_folder",
                        "arguments": {"path": "test_folder"},
                    }
                }],
            }

    assistant = Assistant.__new__(Assistant)
    assistant.policy = ToolExecutionPolicy(max_tool_rounds=2, max_tool_calls=4)
    assistant.client = UnknownToolClient()
    registry = ToolRegistry(tmp_path / "workspace")

    result = assistant._agent_loop(
        [{"role": "user", "content": "delete test_folder"}],
        registry,
        confirm=lambda *_: True,
        user_text="delete test_folder",
    )

    assert "Filesystem operation failed" in result
    assert "not registered" in result
    assert assistant.client.calls == 1
