"""Health metrics for F#, pinned on code from FAKE and FSharp.Data.

Each number below was counted by hand from the source. NLOC counts the body,
the lines after ``=``. The fixtures keep to ``if`` / ``for``: ``match`` arms
and ``&&`` / ``||`` are counted differently by work still in flight. The
skipped tests hold the values main gets wrong today.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file

# FAKE Fake.Core.String/String.fs NormalizeVersion.
_FAKE = r"""module String =
    let rec NormalizeVersion (version: string) =
        if isNull version then
            ""
        else
            let elements = version.Split [| '.' |]
            let mutable version = ""

            for i in 0..3 do
                if i < elements.Length then
                    if version = "" then
                        version <- elements[i]
                    else
                        version <- version + "." + elements[i]

            if version.EndsWith ".0" then
                version.Remove(version.Length - 2, 2) |> NormalizeVersion
            else
                version
"""

# FSharp.Data.Html.Core/HtmlParser.fs CharList.
_HTML_PARSER = r"""module HtmlParser =
    type CharList =
        { mutable Contents: StringBuilder }

        static member Empty = { Contents = StringBuilder() }

        override x.ToString() = x.Contents.ToString()

        member x.Cons(c: char) = x.Contents.Append(c) |> ignore
        member x.Length = x.Contents.Length
        member x.Clear() = x.Contents.Clear() |> ignore
"""

# FSharp.Data.Http/Http.fs getCookiesAndManageCookieContainer's signature.
_CURRIED = r"""let getCookiesAndManageCookieContainer
    uri
    responseUri
    (headers: Map<string, string>)
    (cookieContainer: CookieContainer)
    addCookiesToCookieContainer
    silentCookieErrors
    =
    cookieContainer.GetCookies uri
"""


def _walk(source: str):
    fcx = walk_file("src/Sample.fs", "fsharp", source.encode())
    if not fcx.functions:
        pytest.skip("tree-sitter grammar for F# unavailable")
    return fcx


def test_fake_function() -> None:
    [fn] = _walk(_FAKE).functions
    # if, for, if, if, if. The else arm holds for > if > if.
    assert (fn.name, fn.ccn, fn.max_nesting, fn.nloc) == ("NormalizeVersion", 6, 4, 15)
    assert (fn.start_line, fn.end_line) == (2, 19)


def test_record_members() -> None:
    fcx = _walk(_HTML_PARSER)
    assert {f.name for f in fcx.functions} == {"Empty", "ToString", "Cons", "Length", "Clear"}
    [cls] = fcx.classes
    # Five members; the blank lines are not code.
    assert (cls.method_count, cls.total_nloc) == (5, 7)


# FsCheck-style module: a module-level value, a function, and a type whose
# class-level ``let`` binds a value.
_VALUE_BINDINGS = """module M
let Colon = ','
let f x = x

type C() =
    let mutable n = 1
    member x.M() = n
"""


def test_value_bindings_are_not_units() -> None:
    fcx = _walk(_VALUE_BINDINGS)
    # ``let Colon`` (module level) and ``let mutable n`` (class level) are value
    # bindings, not functions, so only ``member M`` and ``let f x`` are units.
    assert sorted(f.name for f in fcx.functions) == ["M", "f"]
    # The class-level ``let`` no longer inflates the method count.
    [cls] = fcx.classes
    assert cls.method_count == 1


@pytest.mark.skip(reason="curried and tupled F# parameters are not counted: param_count is 0")
def test_parameters() -> None:
    params = {f.name: f.param_count for f in [*_walk(_CURRIED).functions, *_walk(_FAKE).functions]}
    assert params == {"getCookiesAndManageCookieContainer": 6, "NormalizeVersion": 1}


@pytest.mark.skip(reason="an F# type is named <anonymous>: its type_name is not read")
def test_type_name() -> None:
    [cls] = _walk(_HTML_PARSER).classes
    assert cls.name == "CharList"


@pytest.mark.skip(reason="elif_expression is not a branch kind, so else if adds no CCN")
def test_else_if() -> None:
    source = "let f (a: int) (b: int) =\n    if a > 0 then 1\n    elif b > 0 then 2\n    else 3\n"
    [fn] = _walk(source).functions
    assert fn.ccn == 3


@pytest.mark.skip(reason="a following /// doc comment joins the function's span and NLOC")
def test_trailing_doc_comment() -> None:
    source = _FAKE + "\n    /// <summary>\n    /// The colon character\n    /// </summary>\n    let Colon = ','\n"
    fn = next(f for f in _walk(source).functions if f.name == "NormalizeVersion")
    assert (fn.end_line, fn.nloc) == (19, 15)
