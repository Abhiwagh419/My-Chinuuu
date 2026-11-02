from pathlib import Path

from chinuuu.config import resolve_workspace_path


def test_relative_workspace_resolves_from_project_root_not_cwd(tmp_path):
    project = tmp_path / "D" / "Projects" / "Chinuuu"
    workspace = resolve_workspace_path("workspace", project)
    assert workspace == (project / "workspace").resolve()


def test_absolute_workspace_is_preserved(tmp_path):
    target = tmp_path / "custom-workspace"
    assert resolve_workspace_path(str(target), tmp_path / "project") == target.resolve()


def test_settings_load_opt_in_web_research_and_application_allowlist(tmp_path):
    from chinuuu.config import load_settings

    config = tmp_path / "config.toml"
    config.write_text(
        '[security]\nallow_external_network_tools = true\n'
        '[apps]\neditor = "C:/Tools/editor.exe"\n',
        encoding="utf-8",
    )
    settings = load_settings(config)
    assert settings.allow_external_network_tools is True
    assert settings.app_allowlist == {"editor": "C:/Tools/editor.exe"}


def test_settings_keep_external_network_disabled_by_default(tmp_path):
    from chinuuu.config import load_settings

    config = tmp_path / "config.toml"
    config.write_text('[assistant]\nname = "Chinuuu"\n', encoding="utf-8")
    assert load_settings(config).allow_external_network_tools is False


def test_project_root_uses_shared_local_appdata_for_frozen_deployments(monkeypatch, tmp_path):
    import sys
    from chinuuu.config import project_root

    executable = tmp_path / "dist" / "Chinuuu" / "Chinuuu.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    local_app_data = tmp_path / "user" / "AppData" / "Local"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    expected = local_app_data / "Chinuuu"
    assert project_root() == expected.resolve()
    assert expected.is_dir()


def test_load_settings_reads_optional_local_embedding_model(tmp_path):
    from chinuuu.config import load_settings

    config = tmp_path / "config.toml"
    config.write_text('[knowledge]\nembedding_model = "nomic-embed-text"\n', encoding="utf-8")
    assert load_settings(config).embedding_model == "nomic-embed-text"
