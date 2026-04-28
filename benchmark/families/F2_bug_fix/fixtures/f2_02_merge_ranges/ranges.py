def merge_ranges(ranges):
    """Merge closed integer ranges that overlap or touch.

    Example:
        [(1, 3), (4, 6)] -> [(1, 6)]
    """
    if not ranges:
        return []

    ordered = sorted(ranges)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged
