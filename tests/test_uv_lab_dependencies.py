"""Static packaging contract for the Windows laboratory default uv profile.

No native SDK imports, MATLAB startup, or instrument operations are needed.
"""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read_toml(name: str) -> dict:
    return tomllib.loads((ROOT / name).read_text(encoding="utf-8"))


def test_lab_bindings_are_default_on_and_not_optional_extras():
    project = _read_toml("pyproject.toml")
    assert project["tool"]["uv"]["default-groups"] == ["lab"]
    assert project["tool"]["uv"]["dependency-groups"]["lab"]["requires-python"] == ">=3.12,<3.13"

    extras = project["project"]["optional-dependencies"]
    assert "camera" not in extras
    assert "matlab" not in extras
    assert set(extras) == {"test", "dev", "lint", "typecheck"}

    lab = project["dependency-groups"]["lab"]
    assert len(lab) == 2
    assert any(requirement.startswith("vmbpy==1.2.2;") for requirement in lab)
    assert any(requirement.startswith("matlabengine==24.2.2;") for requirement in lab)
    assert all("sys_platform == 'win32'" in requirement for requirement in lab)
    assert all("platform_machine == 'AMD64'" in requirement for requirement in lab)


def test_uv_lock_tracks_lab_group_without_native_runtime():
    lock = _read_toml("uv.lock")
    root = next(package for package in lock["package"] if package["name"] == "iopanel")
    assert set(root["dev-dependencies"]) == {"lab"}
    assert {entry["name"] for entry in root["dev-dependencies"]["lab"]} == {"vmbpy", "matlabengine"}
    assert {entry["name"] for entry in root["metadata"]["requires-dev"]["lab"]} == {"vmbpy", "matlabengine"}
    assert "camera" not in root["optional-dependencies"]
    assert "matlab" not in root["optional-dependencies"]

    versions = {package["name"]: package["version"] for package in lock["package"]}
    assert versions["vmbpy"] == "1.2.2"
    assert versions["matlabengine"] == "24.2.2"
