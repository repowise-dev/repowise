"""Unit tests for CommunityToolkit MVVM synthetic-symbol extraction."""

from __future__ import annotations

from datetime import datetime

import pytest

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser


def _file(path: str = "Vm.cs") -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/tmp/{path}",
        language="csharp",
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


@pytest.fixture(scope="module")
def parser() -> ASTParser:
    return ASTParser()


class TestMvvmObservableProperty:
    def test_observable_property_synthesises_pascal_property(self, parser: ASTParser) -> None:
        src = b"""\
public partial class Vm {
    [ObservableProperty]
    private string _userName;
}
"""
        result = parser.parse_file(_file(), src)
        names = {s.name for s in result.symbols}
        assert "UserName" in names
        prop = next(s for s in result.symbols if s.name == "UserName")
        assert prop.parent_name == "Vm"
        assert prop.visibility == "public"

    def test_field_without_underscore_strips_correctly(self, parser: ASTParser) -> None:
        src = b"""\
public partial class Vm {
    [ObservableProperty]
    private string name;
}
"""
        result = parser.parse_file(_file(), src)
        # When the field is already PascalCase-equivalent the synthetic
        # name would collide with the field — skipped, not duplicated.
        # ``name`` → ``Name`` is a real change; both symbols should exist.
        names = {s.name for s in result.symbols}
        assert "Name" in names

    def test_no_attribute_no_synthesis(self, parser: ASTParser) -> None:
        src = b"""\
public class Vm {
    private string _userName;
}
"""
        result = parser.parse_file(_file(), src)
        names = {s.name for s in result.symbols}
        assert "UserName" not in names


class TestMvvmRelayCommand:
    def test_relay_command_synthesises_command_method(self, parser: ASTParser) -> None:
        src = b"""\
public partial class Vm {
    [RelayCommand]
    private void Save() { }
}
"""
        result = parser.parse_file(_file(), src)
        names = {s.name for s in result.symbols}
        assert "SaveCommand" in names
        cmd = next(s for s in result.symbols if s.name == "SaveCommand")
        assert cmd.parent_name == "Vm"

    def test_relay_command_with_arguments(self, parser: ASTParser) -> None:
        src = b"""\
public partial class Vm {
    [RelayCommand(CanExecute = nameof(CanReset))]
    private void Reset() { }
}
"""
        result = parser.parse_file(_file(), src)
        names = {s.name for s in result.symbols}
        assert "ResetCommand" in names

    def test_non_csharp_returns_no_synthetics(self, parser: ASTParser) -> None:
        """The synthetic pass dispatches by language tag — other languages no-op."""
        fi = FileInfo(
            path="vm.py",
            abs_path="/tmp/vm.py",
            language="python",
            size_bytes=10,
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        result = parser.parse_file(fi, b"def Save(): pass\n")
        # Nothing should have been added beyond the real ``Save``.
        assert [s.name for s in result.symbols] == ["Save"]


class TestExtensionBlocks:
    """C# 14 ``extension(...)`` blocks, which the grammar reads as a constructor."""

    _SRC = b"""\
public static class StringExtensions
{
    extension(string value)
    {
        public bool IsBlank()
        {
            return string.IsNullOrWhiteSpace(value);
        }

        public bool StartsWithAny(char first, char second) => value.Length > 0;

        public static string Create(int count)
        {
            return new string('x', count);
        }
    }
}
"""

    def _methods(self, parser: ASTParser, src: bytes) -> dict[str, object]:
        result = parser.parse_file(_file("StringExtensions.cs"), src)
        return {s.name: s for s in result.symbols if s.kind == "method"}

    def test_block_members_become_extension_methods_of_the_holder(
        self, parser: ASTParser
    ) -> None:
        methods = self._methods(parser, self._SRC)
        assert "extension" not in methods
        blank = methods["IsBlank"]
        assert blank.parent_name == "StringExtensions"
        assert blank.signature == "IsBlank(this string value) -> bool"
        assert blank.visibility == "public"
        assert "static" in blank.modifiers
        assert methods["StartsWithAny"].signature == (
            "StartsWithAny(this string value, char first, char second) -> bool"
        )

    def test_static_block_member_has_no_receiver(self, parser: ASTParser) -> None:
        create = self._methods(parser, self._SRC)["Create"]
        assert create.parent_name == "StringExtensions"
        assert create.signature == "Create(int count) -> string"
        assert "static" in create.modifiers

    def test_classic_extension_method_is_unchanged(self, parser: ASTParser) -> None:
        src = b"""\
public static class ClassicExtensions
{
    public static bool IsShort(this string value) { return value.Length < 3; }
}
"""
        methods = self._methods(parser, src)
        assert set(methods) == {"IsShort"}
        assert methods["IsShort"].signature == "IsShort(this string value) -> bool"

    def test_a_block_that_does_not_parse_is_left_alone(self, parser: ASTParser) -> None:
        # The grammar cannot read an extension property, so the block is left
        # as the symbol pass found it.
        src = b"""\
public static class StringExtensions
{
    extension(string value)
    {
        public bool IsEmpty => value.Length == 0;
    }
}
"""
        assert set(self._methods(parser, src)) == {"extension"}

    def test_a_real_constructor_is_kept(self, parser: ASTParser) -> None:
        # Not a static class, so ``extension(...)`` here is a real constructor
        # of a class named ``extension``.
        src = b"""\
public class extension
{
    public extension(int size) { }
}
"""
        result = parser.parse_file(_file("Extension.cs"), src)
        assert any(s.name == "extension" and s.kind == "method" for s in result.symbols)
