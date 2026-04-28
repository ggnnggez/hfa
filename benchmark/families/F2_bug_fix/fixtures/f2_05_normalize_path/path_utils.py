def normalize_path(path: str) -> str:
    """Normalize slash-separated paths without touching the filesystem."""
    parts = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)
