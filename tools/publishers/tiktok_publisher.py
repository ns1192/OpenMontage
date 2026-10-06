"""TikTok publisher — uploads a finished vertical video via the official
TikTok Content Posting API (https://developers.tiktok.com/doc/content-posting-api-get-started).

Modes:
- ``direct``: Direct Post (``video.publish`` scope). Unaudited developer apps can
  only post with ``visibility: private`` (SELF_ONLY).
- ``draft``: upload to the creator's TikTok inbox (``video.upload`` scope) so the
  creator finishes and publishes in the TikTok app.

Requires ``TIKTOK_ACCESS_TOKEN`` — a user access token obtained via TikTok OAuth.
Use ``dry_run: true`` to validate inputs without any network call.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)

API_BASE = "https://open.tiktokapis.com/v2"
PRIVACY_MAP = {
    "public": "PUBLIC_TO_EVERYONE",
    "unlisted": "MUTUAL_FOLLOW_FRIENDS",
    "private": "SELF_ONLY",
}
MAX_SINGLE_CHUNK = 64 * 1024 * 1024
CHUNK_SIZE = 10 * 1024 * 1024
MAX_CAPTION = 2200
TERMINAL_OK = {"PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"}


class TikTokPublisher(BaseTool):
    name = "tiktok_publisher"
    version = "0.1.0"
    tier = ToolTier.PUBLISH
    capability = "publish"
    provider = "tiktok"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = []
    install_instructions = (
        "Create an app at https://developers.tiktok.com with the Content Posting API "
        "product, authorize a user via OAuth (scopes video.upload / video.publish), and set "
        "TIKTOK_ACCESS_TOKEN in .env."
    )
    agent_skills = []

    capabilities = ["upload_video", "direct_post", "upload_draft", "write_publish_log"]
    supports = {"dry_run": True, "uploads": True, "drafts": True}
    best_for = ["posting finished 9:16 clips to TikTok", "sending clips to the TikTok inbox as drafts"]
    not_good_for = [
        "posting content you do not have rights to",
        "public posting from unaudited apps (TikTok forces private)",
    ]

    input_schema = {
        "type": "object",
        "required": ["video_path"],
        "properties": {
            "video_path": {"type": "string", "description": "Final MP4/MOV/WebM (9:16 recommended)."},
            "title": {"type": "string", "description": "Caption incl. hashtags (max 2200 chars)."},
            "mode": {"type": "string", "enum": ["direct", "draft"], "default": "draft"},
            "visibility": {"type": "string", "enum": ["public", "unlisted", "private"], "default": "private"},
            "disable_comment": {"type": "boolean", "default": False},
            "disable_duet": {"type": "boolean", "default": False},
            "disable_stitch": {"type": "boolean", "default": False},
            "dry_run": {"type": "boolean", "default": False},
            "poll_timeout_seconds": {"type": "number", "default": 120},
        },
    }
    output_schema = {
        "type": "object",
        "properties": {"publish_log": {"type": "object"}, "publish_id": {"type": "string"}},
    }
    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=0, network_required=True
    )
    side_effects = ["uploads the video to the authenticated TikTok account"]
    user_visible_verification = ["Open TikTok (profile or inbox notification) and confirm the post/draft"]

    def _token(self) -> str | None:
        return os.environ.get("TIKTOK_ACCESS_TOKEN")

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if self._token() else ToolStatus.UNAVAILABLE

    @staticmethod
    def _chunks(size: int) -> tuple[int, int]:
        """Return (chunk_size, total_chunk_count) per TikTok's chunking rules."""
        if size <= MAX_SINGLE_CHUNK:
            return size, 1
        return CHUNK_SIZE, size // CHUNK_SIZE  # final chunk absorbs the remainder

    def _validate(self, inputs: dict[str, Any]) -> str | None:
        path = Path(inputs["video_path"]).expanduser()
        if not path.is_file():
            return f"video_path not found: {path}"
        if path.suffix.lower() not in (".mp4", ".mov", ".webm"):
            return "TikTok accepts only MP4, MOV or WebM"
        if len(inputs.get("title", "")) > MAX_CAPTION:
            return f"title exceeds {MAX_CAPTION} characters"
        if inputs.get("mode", "draft") not in ("direct", "draft"):
            return "mode must be 'direct' or 'draft'"
        if inputs.get("visibility", "private") not in PRIVACY_MAP:
            return "visibility must be public, unlisted or private"
        return None

    def dry_run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        err = self._validate(inputs)
        return {"valid": err is None, "error": err, "would_upload": err is None,
                "token_configured": bool(self._token())}

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        err = self._validate(inputs)
        if err:
            return ToolResult(success=False, error=err)
        if inputs.get("dry_run"):
            return ToolResult(success=True, data={"dry_run": True, **self.dry_run(inputs)})
        token = self._token()
        if not token:
            return ToolResult(success=False, error="TIKTOK_ACCESS_TOKEN is not set. " + self.install_instructions)

        import requests

        path = Path(inputs["video_path"]).expanduser()
        size = path.stat().st_size
        chunk_size, total = self._chunks(size)
        mode = inputs.get("mode", "draft")
        visibility = inputs.get("visibility", "private")
        auth = "Bearer " + token
        headers = {"Authorization": auth, "Content-Type": "application/json; charset=UTF-8"}
        body: dict[str, Any] = {
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": chunk_size,
                "total_chunk_count": total,
            }
        }
        if mode == "direct":
            endpoint = f"{API_BASE}/post/publish/video/init/"
            body["post_info"] = {
                "title": inputs.get("title", ""),
                "privacy_level": PRIVACY_MAP[visibility],
                "disable_comment": bool(inputs.get("disable_comment")),
                "disable_duet": bool(inputs.get("disable_duet")),
                "disable_stitch": bool(inputs.get("disable_stitch")),
            }
        else:
            endpoint = f"{API_BASE}/post/publish/inbox/video/init/"

        try:
            resp = requests.post(endpoint, headers=headers, json=body, timeout=60)
            payload = resp.json()
            if resp.status_code >= 400 or payload.get("error", {}).get("code", "ok") != "ok":
                return ToolResult(success=False, error=f"TikTok init failed: {payload.get('error') or resp.text}")
            publish_id = payload["data"]["publish_id"]
            upload_url = payload["data"]["upload_url"]

            with open(path, "rb") as fh:
                for i in range(total):
                    start = i * chunk_size
                    data = fh.read(chunk_size if i < total - 1 else size - start)
                    end = start + len(data) - 1
                    up = requests.put(
                        upload_url,
                        data=data,
                        headers={
                            "Content-Type": {".mov": "video/quicktime", ".webm": "video/webm"}.get(
                                path.suffix.lower(), "video/mp4"),
                            "Content-Length": str(len(data)),
                            "Content-Range": f"bytes {start}-{end}/{size}",
                        },
                        timeout=300,
                    )
                    if up.status_code not in (200, 201, 206):
                        return ToolResult(success=False, error=f"chunk {i + 1}/{total} upload failed: HTTP {up.status_code}")

            status, fail_reason = self._poll(requests, headers, publish_id,
                                             float(inputs.get("poll_timeout_seconds", 120)))
        except requests.RequestException as exc:
            return ToolResult(success=False, error=f"TikTok request failed: {exc}")

        if status in TERMINAL_OK:
            log_status = "published" if status == "PUBLISH_COMPLETE" else "draft"
        elif status == "FAILED":
            return ToolResult(success=False, error=f"TikTok processing failed: {fail_reason}")
        else:
            log_status = "pending_review"  # still processing at timeout

        entry = {
            "platform": "tiktok",
            "status": log_status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "visibility": visibility,
            "metadata_used": {"title": inputs.get("title", ""), "publish_id": publish_id, "mode": mode,
                              "tiktok_status": status},
        }
        publish_log = {"version": "1.0", "entries": [entry]}
        try:
            from schemas.artifacts import validate_artifact

            validate_artifact("publish_log", publish_log)
        except Exception as exc:  # pragma: no cover - defensive
            return ToolResult(success=False, error=f"publish_log failed schema validation: {exc}")
        return ToolResult(success=True, data={"publish_log": publish_log, "publish_id": publish_id,
                                              "tiktok_status": status})

    @staticmethod
    def _poll(requests, headers: dict, publish_id: str, timeout: float) -> tuple[str, str | None]:
        deadline = time.monotonic() + timeout
        status, reason = "PROCESSING_UPLOAD", None
        while True:
            r = requests.post(f"{API_BASE}/post/publish/status/fetch/", headers=headers,
                              json={"publish_id": publish_id}, timeout=30)
            d = r.json().get("data", {})
            status, reason = d.get("status", status), d.get("fail_reason")
            if status in TERMINAL_OK or status == "FAILED" or time.monotonic() >= deadline:
                return status, reason
            time.sleep(3)
