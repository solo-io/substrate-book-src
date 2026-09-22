def slugify(value: str) -> str:
    """Turn a title into a URL slug."""
    return value.strip().lower().replace(" ", "-")

