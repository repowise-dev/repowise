def _is_generated(file_content: str) -> bool:
    # Check if file content matches any of the generated markers
    return file_content and any(marker in file_content for marker in _GENERATED_MARKERS)
