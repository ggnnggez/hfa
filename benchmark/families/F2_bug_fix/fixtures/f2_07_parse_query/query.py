def parse_query(query: str) -> dict[str, str]:
    """Parse a URL query string into a dict."""
    result = {}
    for pair in query.split("&"):
        key, value = pair.split("=")
        result[key] = value
    return result
