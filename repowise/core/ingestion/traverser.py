def _is_generated(file_content):
  return any(marker in file_content for marker in _GENERATED_MARKERS)
