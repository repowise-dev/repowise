"""Unit tests for JSX prop-guard dead code detection (#1554)."""

from __future__ import annotations

from repowise.core.analysis.dead_code import (
    DeadCodeAnalyzer,
    DeadCodeKind,
)
from tests.unit.dead_code._helpers import _build_graph


def test_jsx_prop_guard_unsatisfied_reported(tmp_path):
    """Component B behind flag in Component A should be reported when A is rendered without flag."""
    a_content = """
function ComponentA(props) {
    return <div>{props.flag && <ComponentB />}</div>;
}
"""
    b_content = """
export function ComponentB() {
    return <div>Hello</div>;
}
"""
    app_content = """
function App() {
    return <ComponentA />;
}
"""
    a_file = tmp_path / "ComponentA.tsx"
    b_file = tmp_path / "ComponentB.tsx"
    app_file = tmp_path / "App.tsx"

    a_file.write_text(a_content)
    b_file.write_text(b_content)
    app_file.write_text(app_content)

    g = _build_graph(
        nodes={
            str(app_file): {
                "is_entry_point": True,
                "is_test": False,
                "symbols": [{"name": "App", "kind": "function", "visibility": "public"}],
            },
            str(a_file): {
                "is_entry_point": False,
                "is_test": False,
                "symbols": [{"name": "ComponentA", "kind": "function", "visibility": "public"}],
            },
            str(b_file): {
                "is_entry_point": False,
                "is_test": False,
                "symbols": [{"name": "ComponentB", "kind": "function", "visibility": "public"}],
            },
        },
        edges=[
            (str(app_file), str(a_file), {"imported_names": ["ComponentA"]}),
            (str(a_file), str(b_file), {"imported_names": ["ComponentB"]}),
            (
                f"{app_file}::App",
                f"{a_file}::ComponentA",
                {"edge_type": "calls", "confidence": 0.95, "supplied_props": frozenset()},
            ),
            (
                f"{a_file}::ComponentA",
                f"{b_file}::ComponentB",
                {"edge_type": "calls", "confidence": 0.95, "supplied_props": frozenset()},
            ),
        ],
    )

    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_zombie_packages": False,
        }
    )

    unused = [f for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT]
    sym_names = [f.symbol_name for f in unused]
    assert "ComponentB" in sym_names

    finding = next(f for f in unused if f.symbol_name == "ComponentB")
    assert finding.confidence == 0.4
    assert finding.safe_to_delete is False
    assert "flag" in finding.reason


def test_jsx_prop_guard_satisfied_not_reported(tmp_path):
    """Component B should NOT be reported when Component A is rendered with flag."""
    a_content = """
function ComponentA(props) {
    return <div>{props.flag && <ComponentB />}</div>;
}
"""
    b_content = """
export function ComponentB() {
    return <div>Hello</div>;
}
"""
    app_content = """
function App() {
    return <ComponentA flag={true} />;
}
"""
    a_file = tmp_path / "ComponentA.tsx"
    b_file = tmp_path / "ComponentB.tsx"
    app_file = tmp_path / "App.tsx"

    a_file.write_text(a_content)
    b_file.write_text(b_content)
    app_file.write_text(app_content)

    g = _build_graph(
        nodes={
            str(app_file): {
                "is_entry_point": True,
                "is_test": False,
                "symbols": [{"name": "App", "kind": "function", "visibility": "public"}],
            },
            str(a_file): {
                "is_entry_point": False,
                "is_test": False,
                "symbols": [{"name": "ComponentA", "kind": "function", "visibility": "public"}],
            },
            str(b_file): {
                "is_entry_point": False,
                "is_test": False,
                "symbols": [{"name": "ComponentB", "kind": "function", "visibility": "public"}],
            },
        },
        edges=[
            (str(app_file), str(a_file), {"imported_names": ["ComponentA"]}),
            (str(a_file), str(b_file), {"imported_names": ["ComponentB"]}),
            (
                f"{app_file}::App",
                f"{a_file}::ComponentA",
                {"edge_type": "calls", "confidence": 0.95, "supplied_props": frozenset(["flag"])},
            ),
            (
                f"{a_file}::ComponentA",
                f"{b_file}::ComponentB",
                {"edge_type": "calls", "confidence": 0.95, "supplied_props": frozenset()},
            ),
        ],
    )

    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_zombie_packages": False,
        }
    )

    unused = [f for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT]
    sym_names = [f.symbol_name for f in unused]
    assert "ComponentB" not in sym_names


def test_jsx_prop_guard_spread_not_reported(tmp_path):
    """Component B should NOT be reported when Component A is rendered with spread props."""
    a_content = """
function ComponentA(props) {
    return <div>{props.flag && <ComponentB />}</div>;
}
"""
    b_content = """
export function ComponentB() {
    return <div>Hello</div>;
}
"""
    app_content = """
function App(props) {
    return <ComponentA {...props} />;
}
"""
    a_file = tmp_path / "ComponentA.tsx"
    b_file = tmp_path / "ComponentB.tsx"
    app_file = tmp_path / "App.tsx"

    a_file.write_text(a_content)
    b_file.write_text(b_content)
    app_file.write_text(app_content)

    g = _build_graph(
        nodes={
            str(app_file): {
                "is_entry_point": True,
                "is_test": False,
                "symbols": [{"name": "App", "kind": "function", "visibility": "public"}],
            },
            str(a_file): {
                "is_entry_point": False,
                "is_test": False,
                "symbols": [{"name": "ComponentA", "kind": "function", "visibility": "public"}],
            },
            str(b_file): {
                "is_entry_point": False,
                "is_test": False,
                "symbols": [{"name": "ComponentB", "kind": "function", "visibility": "public"}],
            },
        },
        edges=[
            (str(app_file), str(a_file), {"imported_names": ["ComponentA"]}),
            (str(a_file), str(b_file), {"imported_names": ["ComponentB"]}),
            (
                f"{app_file}::App",
                f"{a_file}::ComponentA",
                {"edge_type": "calls", "confidence": 0.95, "supplied_props": None},
            ),
            (
                f"{a_file}::ComponentA",
                f"{b_file}::ComponentB",
                {"edge_type": "calls", "confidence": 0.95, "supplied_props": frozenset()},
            ),
        ],
    )

    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_zombie_packages": False,
        }
    )

    unused = [f for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT]
    sym_names = [f.symbol_name for f in unused]
    assert "ComponentB" not in sym_names


def test_jsx_prop_guard_namespaced_reported(tmp_path):
    """Namespaced component <UI.Button /> behind prop guard should be detected."""
    a_content = """
function ComponentA(props) {
    return <div>{props.show && <UI.Button />}</div>;
}
"""
    b_content = """
export function Button() {
    return <button>Click</button>;
}
"""
    app_content = """
function App() {
    return <ComponentA />;
}
"""
    a_file = tmp_path / "ComponentA.tsx"
    b_file = tmp_path / "Button.tsx"
    app_file = tmp_path / "App.tsx"

    a_file.write_text(a_content)
    b_file.write_text(b_content)
    app_file.write_text(app_content)

    g = _build_graph(
        nodes={
            str(app_file): {
                "is_entry_point": True,
                "is_test": False,
                "symbols": [{"name": "App", "kind": "function", "visibility": "public"}],
            },
            str(a_file): {
                "is_entry_point": False,
                "is_test": False,
                "symbols": [{"name": "ComponentA", "kind": "function", "visibility": "public"}],
            },
            str(b_file): {
                "is_entry_point": False,
                "is_test": False,
                "symbols": [{"name": "Button", "kind": "function", "visibility": "public"}],
            },
        },
        edges=[
            (str(app_file), str(a_file), {"imported_names": ["ComponentA"]}),
            (str(a_file), str(b_file), {"imported_names": ["Button"]}),
            (
                f"{app_file}::App",
                f"{a_file}::ComponentA",
                {"edge_type": "calls", "confidence": 0.95, "supplied_props": frozenset()},
            ),
            (
                f"{a_file}::ComponentA",
                f"{b_file}::Button",
                {"edge_type": "calls", "confidence": 0.95, "supplied_props": frozenset()},
            ),
        ],
    )

    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_zombie_packages": False,
        }
    )

    unused = [f for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT]
    sym_names = [f.symbol_name for f in unused]
    assert "Button" in sym_names


def test_jsx_prop_guard_negated_not_reported(tmp_path):
    """Component B behind !show guard renders when show is absent and should NOT be reported as dead."""
    a_content = """
function ComponentA(props) {
    return <div>{!props.show && <ComponentB />}</div>;
}
"""
    b_content = """
export function ComponentB() {
    return <div>Hello</div>;
}
"""
    app_content = """
function App() {
    return <ComponentA />;
}
"""
    a_file = tmp_path / "ComponentA.tsx"
    b_file = tmp_path / "ComponentB.tsx"
    app_file = tmp_path / "App.tsx"

    a_file.write_text(a_content)
    b_file.write_text(b_content)
    app_file.write_text(app_content)

    g = _build_graph(
        nodes={
            str(app_file): {
                "is_entry_point": True,
                "is_test": False,
                "symbols": [{"name": "App", "kind": "function", "visibility": "public"}],
            },
            str(a_file): {
                "is_entry_point": False,
                "is_test": False,
                "symbols": [{"name": "ComponentA", "kind": "function", "visibility": "public"}],
            },
            str(b_file): {
                "is_entry_point": False,
                "is_test": False,
                "symbols": [{"name": "ComponentB", "kind": "function", "visibility": "public"}],
            },
        },
        edges=[
            (str(app_file), str(a_file), {"imported_names": ["ComponentA"]}),
            (str(a_file), str(b_file), {"imported_names": ["ComponentB"]}),
            (
                f"{app_file}::App",
                f"{a_file}::ComponentA",
                {"edge_type": "calls", "confidence": 0.95, "supplied_props": frozenset()},
            ),
            (
                f"{a_file}::ComponentA",
                f"{b_file}::ComponentB",
                {"edge_type": "calls", "confidence": 0.95, "supplied_props": frozenset()},
            ),
        ],
    )

    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_zombie_packages": False,
        }
    )

    unused = [f for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT]
    sym_names = [f.symbol_name for f in unused]
    assert "ComponentB" not in sym_names


def test_jsx_prop_guard_root_component_not_reported(tmp_path):
    """Component B behind guard in root component (no incoming callers) should NOT be falsely reported."""
    a_content = """
function ComponentA(props) {
    return <div>{props.flag && <ComponentB />}</div>;
}
"""
    b_content = """
export function ComponentB() {
    return <div>Hello</div>;
}
"""
    a_file = tmp_path / "ComponentA.tsx"
    b_file = tmp_path / "ComponentB.tsx"

    a_file.write_text(a_content)
    b_file.write_text(b_content)

    g = _build_graph(
        nodes={
            str(a_file): {
                "is_entry_point": False,
                "is_test": False,
                "symbols": [{"name": "ComponentA", "kind": "function", "visibility": "public"}],
            },
            str(b_file): {
                "is_entry_point": False,
                "is_test": False,
                "symbols": [{"name": "ComponentB", "kind": "function", "visibility": "public"}],
            },
        },
        edges=[
            (str(a_file), str(b_file), {"imported_names": ["ComponentB"]}),
            (
                f"{a_file}::ComponentA",
                f"{b_file}::ComponentB",
                {"edge_type": "calls", "confidence": 0.95, "supplied_props": frozenset()},
            ),
        ],
    )

    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_zombie_packages": False,
        }
    )

    unused = [f for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT]
    sym_names = [f.symbol_name for f in unused]
    assert "ComponentB" not in sym_names
