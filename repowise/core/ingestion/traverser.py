def _is_generated(file_content: str) -> bool:
    # Modified to exclude 'AUTO-GENERATED' marker in standalone context
    return any(marker in file_content for marker in _GENERATED_MARKERS if marker != "AUTO-GENERATED" and file_content.strip().startswith(marker))