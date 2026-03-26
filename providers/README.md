# Community Providers

This directory contains configuration examples and documentation for community-contributed
repowise providers. Built-in providers are in `packages/core/src/repowise/core/providers/`.

## Adding a Custom Provider

See [CONTRIBUTING.md](../docs/CONTRIBUTING.md) for a step-by-step guide on adding a new provider.

The short version:
1. Create `packages/core/src/repowise/core/providers/my_provider.py`
2. Subclass `BaseProvider`, implement `generate()`, `provider_name`, `model_name`
3. Register in `registry.py`
4. Add tests in `tests/providers/test_my_provider.py`

Or, for a provider that doesn't need to be in the core package, use `register_provider()`:

```python
from repowise.core.providers import register_provider
from my_package import MyCustomProvider

register_provider("my_provider", lambda **kw: MyCustomProvider(**kw))
```
