from pathlib import Path


def test_project_is_downloader_only() -> None:
    root = Path(__file__).resolve().parents[1]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")

    assert "abyss-download" in pyproject
    assert "abyss-stream" not in pyproject
    assert not (root / "abyssdl" / "web.py").exists()
    assert not (root / "stream_server.py").exists()
    assert not (root / "stream.bat").exists()
