import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.publishers.tiktok_publisher import TikTokPublisher
from tools.tool_registry import ToolRegistry


def test_registered():
    reg = ToolRegistry()
    reg.discover()
    assert reg.get("tiktok_publisher") is not None


def test_dry_run_and_validation(tmp_path):
    v = tmp_path / "a.mp4"
    v.write_bytes(b"x")
    t = TikTokPublisher()
    assert t.execute({"video_path": str(v), "dry_run": True}).success
    assert not t.execute({"video_path": str(tmp_path / "missing.mp4")}).success
    assert not t.execute({"video_path": str(v), "title": "x" * 3000}).success


def test_missing_token(tmp_path, monkeypatch):
    monkeypatch.delenv("TIKTOK_ACCESS_TOKEN", raising=False)
    v = tmp_path / "a.mp4"
    v.write_bytes(b"x")
    r = TikTokPublisher().execute({"video_path": str(v)})
    assert not r.success and "TIKTOK_ACCESS_TOKEN" in r.error


def test_chunks():
    assert TikTokPublisher._chunks(1000) == (1000, 1)
    assert TikTokPublisher._chunks(25 * 1024 * 1024 * 3) [1] == 7
