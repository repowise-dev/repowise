"""Apple runtime metadata roots declarations, not similarly named files."""

import plistlib

import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.pipeline.incremental import build_repo_graph


def _write(root, files):
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text if isinstance(text, bytes) else text.encode())


def _analyze(root):
    _, sources, builder, _, _ = build_repo_graph(
        root, [], collect_sources=True, read_only=True, log=lambda *a, **k: None
    )
    graph = builder.graph()
    report = DeadCodeAnalyzer(
        graph, {}, parsed_files=builder._parsed_files, source_map=sources, repo_root=root
    ).analyze({"min_confidence": 0.0})
    dead = {f.file_path for f in report.findings if f.kind is DeadCodeKind.UNREACHABLE_FILE}
    return graph, dead


def test_extension_storyboard_and_xib_roots(tmp_path):
    _write(tmp_path, {
        "App/App.swift": "@main struct MainApp { static func main() {} }",
        "ShareExtension/Controller.swift": "class ShareViewController: UIViewController {}",
        "ShareExtension/Info.plist": plistlib.dumps({"NSExtension": {
            "NSExtensionPrincipalClass": "$(PRODUCT_MODULE_NAME).ShareViewController"}}),
        "Widgets/Views.swift": "class SettingsViewController: UIViewController {}",
        "Widgets/Base.lproj/Main.storyboard": '<document><viewController customClass="SettingsViewController" customModule="Widgets" /></document>',
        "Widgets/Unrelated.swift": "class Unrelated: UIViewController {}",
        "Mac/Panel.m": "@interface PanelController : NSObject\n@end\n@implementation PanelController\n@end",
        "Mac/Panel.xib": '<document><customObject customClass="PanelController" /></document>',
    })
    graph, dead = _analyze(tmp_path)
    for path in ("ShareExtension/Controller.swift", "Widgets/Views.swift", "Mac/Panel.m"):
        assert path not in dead
        assert graph.nodes[path]["is_entry_point"]
        assert graph.has_edge("framework:apple", path)
    assert graph.nodes["App/App.swift"]["is_entry_point"]
    assert "Widgets/Unrelated.swift" in dead


@pytest.mark.parametrize("key", ["NSPrincipalClass", "UISceneDelegateClassName", "NSExtensionClass"])
def test_all_plist_keys_prefer_same_target(tmp_path, key):
    _write(tmp_path, {
        "First/Sources/Implementation.swift": "class Delegate: NSObject {}",
        "Second/Implementation.swift": "class Delegate: NSObject {}",
        "First/First-Info.plist": plistlib.dumps({"Nested": [{key: "First.Delegate"}]}),
    })
    graph, _ = _analyze(tmp_path)
    assert graph.nodes["First/Sources/Implementation.swift"].get("is_entry_point")
    assert not graph.nodes["Second/Implementation.swift"].get("is_entry_point")


def test_custom_module_overrides_metadata_folder(tmp_path):
    _write(tmp_path, {
        "First/View.swift": "class Panel: UIViewController {}",
        "Second/View.swift": "class Panel: UIViewController {}",
        "First/Base.lproj/Main.xib": '<document><view customClass="Panel" customModule="Second" /></document>',
    })
    graph, _ = _analyze(tmp_path)
    assert graph.nodes["Second/View.swift"].get("is_entry_point")
    assert not graph.nodes["First/View.swift"].get("is_entry_point")


def test_ambiguous_names_binary_and_invalid_metadata_do_not_root(tmp_path):
    _write(tmp_path, {
        "First/View.swift": "class Duplicate: UIViewController {}",
        "Second/View.swift": "class Duplicate: UIViewController {}",
        "Resources/Info.plist": plistlib.dumps({"NSPrincipalClass": "Duplicate"}),
        "Binary/Info.plist": plistlib.dumps({"NSPrincipalClass": "BinaryOnly"}, fmt=plistlib.FMT_BINARY),
        "Binary/Class.swift": "class BinaryOnly: NSObject {}",
        "Broken/Info.plist": "not a plist",
        "Broken/View.xib": "<broken",
    })
    graph, _ = _analyze(tmp_path)
    assert all(not graph.nodes[path].get("is_entry_point") for path in (
        "First/View.swift", "Second/View.swift", "Binary/Class.swift"))


def test_unique_declaration_outside_metadata_target_is_rooted(tmp_path):
    _write(tmp_path, {
        "Shared/NotTheClassName.swift": "class OnlyController: UIViewController {}",
        "Resources/Info.plist": plistlib.dumps({"NSPrincipalClass": "OnlyController"}),
    })
    graph, _ = _analyze(tmp_path)
    assert graph.nodes["Shared/NotTheClassName.swift"].get("is_entry_point")


def test_metadata_uses_declared_classes_not_filenames_or_arbitrary_strings(tmp_path):
    _write(tmp_path, {
        "App/Controller.swift": "class Unnamed: UIViewController {}",
        "App/Other.swift": "class NotAClassKey: NSObject {}",
        "App/Info.plist": plistlib.dumps({
            "NSPrincipalClass": "Controller", "CFBundleName": "NotAClassKey"}),
        "App/View.xib": '<document><label text="Unnamed" /></document>',
    })
    graph, _ = _analyze(tmp_path)
    assert not graph.nodes["App/Controller.swift"].get("is_entry_point")
    assert not graph.nodes["App/Other.swift"].get("is_entry_point")


def test_nested_repo_metadata_is_not_loaded(tmp_path):
    _write(tmp_path, {
        "App/Class.swift": "class NestedOnly: NSObject {}",
        "Vendor/.git/HEAD": "ref: refs/heads/main",
        "Vendor/Info.plist": plistlib.dumps({"NSPrincipalClass": "NestedOnly"}),
    })
    graph, _ = _analyze(tmp_path)
    assert not graph.nodes["App/Class.swift"].get("is_entry_point")


def test_objc_header_and_implementation_are_one_class_not_an_ambiguity(tmp_path):
    _write(tmp_path, {
        "Mac/Panel.h": "@interface Panel : NSObject\n@end",
        "Mac/Panel.m": '#import "Panel.h"\n@implementation Panel\n@end',
        "Mac/Info.plist": plistlib.dumps({"NSPrincipalClass": "Panel"}),
    })
    graph, dead = _analyze(tmp_path)
    assert graph.nodes["Mac/Panel.m"].get("is_entry_point")
    assert "Mac/Panel.m" not in dead


def test_unselected_plist_name_is_not_assumed_to_be_runtime_metadata(tmp_path):
    _write(tmp_path, {
        "App/Controller.swift": "class Unselected: NSObject {}",
        "App/Config.plist": plistlib.dumps({"NSPrincipalClass": "Unselected"}),
    })
    graph, dead = _analyze(tmp_path)
    assert "App/Controller.swift" in dead
    assert not graph.nodes["App/Controller.swift"].get("is_entry_point")
