from __future__ import annotations


def rewrite_camera_absolute_path(*, ftp_path: str, camera_id: str) -> str:
    """Rewrite camera "absolute" upload paths into the jailed root.

    Some cameras are configured with a server directory like `/data/incoming/<camera_id>`.
    Because the user is jailed to `incoming/<camera_id>`, that creates a duplicated nested tree:
      incoming/<camera_id>/data/incoming/<camera_id>/...

    We rewrite the common absolute prefixes back to `/` so files land in the intended root.
    """

    if not ftp_path:
        return ftp_path

    p = str(ftp_path).strip()

    # Some cameras first `CWD /data` (or `CWD data`) and later use a more specific path.
    # Under a jailed homedir, that creates an extra nested `data/` directory per camera.
    # Rewrite those bare prefixes back to the jail root.
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
