def parse_bool(value):
    """Parse common boolean config values."""
    if isinstance(value, bool):
        return value
    return bool(value)
