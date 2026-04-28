def top_k(items, k, key=lambda item: item):
    """Return the top k items by key, highest first."""
    return sorted(items, key=key)[:k]
