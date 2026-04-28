def moving_average(values, window):
    """Return simple moving averages for each complete window."""
    if window <= 0:
        raise ValueError("window must be positive")
    result = []
    for idx in range(len(values) - window):
        result.append(sum(values[idx:idx + window]) / window)
    return result
