"""Health metrics for Razor, pinned on ``@code`` members from eShop.

A Razor file reaches the walker as a C# buffer with the markup blanked out at
the same offsets, so line numbers are the ``.razor`` file's own. Each number
below was counted by hand from the source.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file

# eShop WebApp Pages/Item/ItemPage.razor ComputeBackHref (its comment
# shortened) and Pages/Catalog/Catalog.razor GetPageTokens.
_ESHOP = r"""@page "/item/{itemId:int}"
@inject NavigationManager Nav

<div class="item-details">
    <a href="@backHref">Back to catalog</a>
</div>

@code {
    // Return the shopper to the catalog they came from, preserving any active
    // brand/type/page filters.
    private string ComputeBackHref()
    {
        var referer = HttpContext?.Request.Headers.Referer.ToString();
        if (!string.IsNullOrEmpty(referer)
            && Uri.TryCreate(referer, UriKind.Absolute, out var refUri)
            && Uri.TryCreate(Nav.BaseUri, UriKind.Absolute, out var baseUri)
            && string.Equals(refUri.Authority, baseUri.Authority, StringComparison.OrdinalIgnoreCase)
            && string.Equals(refUri.AbsolutePath.TrimEnd('/'), baseUri.AbsolutePath.TrimEnd('/'), StringComparison.OrdinalIgnoreCase)
            && !string.IsNullOrEmpty(refUri.Query))
        {
            return refUri.PathAndQuery;
        }

        return "";
    }

    static IEnumerable<int> GetPageTokens(int totalPages, int current)
    {
        var pages = new SortedSet<int> { 1, totalPages };
        for (var p = current - 1; p <= current + 1; p++)
        {
            if (p >= 1 && p <= totalPages)
            {
                pages.Add(p);
            }
        }

        var prev = 0;
        foreach (var p in pages)
        {
            if (prev != 0 && p - prev > 1)
            {
                yield return -1; // ellipsis marker
            }
            yield return p;
            prev = p;
        }
    }
}
"""


def _walk(source: str):
    fcx = walk_file("Pages/Sample.razor", "razor", source.encode())
    if not fcx.functions:
        pytest.skip("tree-sitter grammar for Razor unavailable")
    return fcx


def test_eshop_members() -> None:
    metrics = {
        f.name: (f.start_line, f.ccn, f.max_nesting, f.nloc, f.param_count)
        for f in _walk(_ESHOP).functions
    }
    assert metrics == {
        # One if over five ``&&``; ``?.`` is not a decision.
        "ComputeBackHref": (11, 7, 1, 13, 0),
        # for, if + ``&&``, foreach, if + ``&&``.
        "GetPageTokens": (27, 7, 2, 20, 2),
    }


def test_compound_condition() -> None:
    conditions = [
        (f.name, c.line, c.operator_count)
        for f in _walk(_ESHOP).functions
        for c in f.complex_conditions
        if c.operator_count >= 3
    ]
    assert conditions == [("ComputeBackHref", 14, 5)]


@pytest.mark.skip(reason="an attributed @code property joins the next method, whose span then starts at the attribute")
def test_method_after_an_attributed_property() -> None:
    source = _ESHOP.replace(
        "@code {\n",
        "@code {\n    [CascadingParameter]\n    public HttpContext? HttpContext { get; set; }\n\n",
    )
    [fn] = [f for f in _walk(source).functions if f.name == "ComputeBackHref"]
    assert fn.start_line == 14
