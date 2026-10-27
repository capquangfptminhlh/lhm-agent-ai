"""Local-only, content-addressed screenshot evidence storage.

Screenshots MUST have already been redacted by the authenticated Runner.
The metadata is safe for SQLite; PNG bytes never go to a model or audit log.
No unredacted screenshot can pass this API (explicit redacted=True required).
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
from pathlib import Path

from fastapi import HTTPException

_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")
_PNG = b"\x89PNG\r\n\x1a\n"
_MAX = 2 * 1024 * 1024


def _name(s: str) -> str:
    if not isinstance(s, str) or not _ID.fullmatch(s):
        raise HTTPException(422, "Invalid evidence identifier")
    return s


def store_redacted_png(run_id: str, step_id: str, phase: str,
                       screenshot: dict, *, db_path: str | None = None) -> dict:
    """Persist a private redacted PNG, atomically and without overwriting."""
    _name(run_id)
    _name(step_id)
    if phase not in ("pre", "post"):
        raise HTTPException(422, "Invalid evidence phase")
    if not isinstance(screenshot, dict) or screenshot.get("redacted") is not True:
        raise HTTPException(403, "Refusing to save a non-redacted screenshot")
    encoded=screenshot.get("png_base64")
    if not isinstance(encoded, str) or not 0 < len(encoded) <= 3_000_000:
        raise HTTPException(422, "Invalid screenshot length")
    try:
        data=base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise HTTPException(422, "Invalid screenshot encoding") from exc
    if not (_PNG == data[:8] and 24 <= len(data) <= _MAX and
            data[12:16] == b"IHDR"):
        raise HTTPException(422, "Invalid or oversized PNG")
    w=int.from_bytes(data[16:20], "big")
    h=int.from_bytes(data[20:24], "big")
    if not (1 <= w <= 8000 and 1 <= h <= 8000):
        raise HTTPException(422, "Invalid screenshot dimensions")
    db=db_path or os.environ.get("LHM_DB_PATH", "lhm.sqlite3")
    root=Path(db).resolve().parent / "evidence"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    directory=root/_name(run_id)
    directory.mkdir(exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    target=directory / f"{_name(step_id)}-{phase}.png"
    # Exclusive open: never replace an earlier audit record after crash/retry.
    try:
        fd=os.open(str(target),os.O_WRONLY | os.O_CREAT | os.O_EXCL,0o600)
    except FileExistsError as exc:
        raise HTTPException(409, "Screenshot evidence already exists") from exc
    try:
        with os.fdopen(fd,"wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(target, 0o600)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return {"path":str(target.relative_to(root.parent)).replace("\\","/"),
            "sha256":hashlib.sha256(data).hexdigest(),
            "bytes":len(data), "redacted":True,
            "width":w, "height":h}
