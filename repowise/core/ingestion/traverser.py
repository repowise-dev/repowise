def _is_generated(self, content):
    return any(marker in content for marker in self._GENERATED_MARKERS)
