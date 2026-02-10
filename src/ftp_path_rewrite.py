from __future__ import annotations


def rewrite_camera_absolute_path(*, ftp_path: str, camera_id: str) -> str:
    """Rewrite camera "absolute" upload paths into the jailed root.

    Cameras are often configured with a server directory like `/data/incoming/<camera_id>`.
    Because the user is jailed to `incoming/<camera_id>`, that can create a duplicated nested tree:
      incoming/<camera_id>/data/incoming/<camera_id>/...

    This function rewrites common absolute prefixes back to `/` so uploads land in the intended root.

    Important: This operates on FTP *virtual* paths (e.g. `/2026/..`, `/data/incoming/<cid>/..`).
    """

    if not ftp_path:
        return ftp_path

    p = str(ftp_path).strip()

    # Some cameras first `CWD /data` (or `CWD data`) and later use a more specific path.
    # Under a jailed homedir, that creates an extra nested `data/` directory per camera.
    p_no_trailing = p.rstrip("/") if p not in {"/", ""} else p
    if p_no_trailing in {"/data", "data"}:
        return "/"

    cid = str(camera_id or "").strip()
    if not cid:
        return p

    prefixes = [
        f"/data/incoming/{cid}",
        f"data/incoming/{cid}",
        f"/incoming/{cid}",
        f"incoming/{cid}",
    ]

    for prefix in prefixes:
        if p == prefix:
            return "/"
        if p.startswith(prefix + "/"):
            remainder = p[len(prefix) :].lstrip("/")
            return "/" + remainder if remainder else "/"

    return p
