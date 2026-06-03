"""Tests for the architectural layer spine (generation.layers)."""

from __future__ import annotations

from repowise.core.generation.layers import (
    DEFAULT_LAYER,
    compute_layer_order,
    infer_layer,
)

# ---------------------------------------------------------------------------
# infer_layer — every file maps to exactly one layer
# ---------------------------------------------------------------------------


def test_infer_layer_matches_directory_hints():
    assert infer_layer("src/api/users.py") == "API"
    assert infer_layer("app/services/billing.py") == "Service"
    assert infer_layer("pkg/models/user.py") == "Data"
    assert infer_layer("web/components/Button.tsx") == "UI"
    assert infer_layer("src/middleware/auth.ts") == "Middleware"
    assert infer_layer("lib/utils/strings.py") == "Utility"
    assert infer_layer("config/settings.py") == "Config"
    assert infer_layer("tests/test_user.py") == "Test"
    assert infer_layer("src/types/dtos.ts") == "Types"


def test_infer_layer_recognizes_cli_command_surface():
    # Edge case A: a CLI command surface must not fall through to Application.
    assert infer_layer("packages/cli/src/repowise/cli/commands/init_cmd.py") == "CLI"
    assert infer_layer("src/cli/main.py") == "CLI"
    assert infer_layer("app/cmd/serve.py") == "CLI"


def test_infer_layer_uses_deepest_matching_directory():
    # The closest directory wins over a shallower one.
    assert infer_layer("services/api/handler.py") == "API"


def test_infer_layer_falls_back_for_unmatched_paths():
    assert infer_layer("main.py") == DEFAULT_LAYER
    assert infer_layer("random/folder/thing.py") == DEFAULT_LAYER


# ---------------------------------------------------------------------------
# compute_layer_order — top→bottom by dependency direction
# ---------------------------------------------------------------------------


def test_compute_layer_order_follows_dependency_direction():
    file_layers = {
        "api/h.py": "API",
        "services/s.py": "Service",
        "models/m.py": "Data",
    }
    # API imports Service imports Data — a clean stack.
    edges = [
        ("api/h.py", "services/s.py"),
        ("services/s.py", "models/m.py"),
    ]
    order = compute_layer_order(file_layers, edges)
    assert order.index("API") < order.index("Service") < order.index("Data")


def test_compute_layer_order_ignores_external_and_intra_layer_edges():
    file_layers = {"api/a.py": "API", "api/b.py": "API", "data/d.py": "Data"}
    edges = [
        ("api/a.py", "api/b.py"),  # intra-layer — ignored
        ("api/a.py", "external:requests"),  # external — ignored
        ("api/a.py", "data/d.py"),  # API → Data
    ]
    order = compute_layer_order(file_layers, edges)
    assert order.index("API") < order.index("Data")


def test_compute_layer_order_stable_without_edges():
    file_layers = {"a.py": "API", "b.py": "Utility", "c.py": "Data"}
    order = compute_layer_order(file_layers, [])
    # Falls back to canonical rank: API above Data above Utility.
    assert order == ["API", "Data", "Utility"]


def test_compute_layer_order_single_layer():
    assert compute_layer_order({"a.py": "API"}, []) == ["API"]
    assert compute_layer_order({}, []) == []
