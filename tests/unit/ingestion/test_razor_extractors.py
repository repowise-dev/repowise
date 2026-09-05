"""Razor/Blazor end-to-end extraction: symbols, calls, health.

A ``.razor`` / ``.cshtml`` file projects its C# regions (``@code`` /
``@functions`` / ``@{ }`` blocks) into a C# buffer at byte-identical
offsets via ``sfc_source``, exactly as a ``.svelte`` file projects into
TypeScript. These tests pin the edges the issue #1404 reporter named:
component instantiations (``<RadzenDataGrid />``) and call edges from
``@code`` bodies (``Service.Method()``), plus the component symbol the
file itself declares.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser

_COMPONENT = b"""@page "/orders"
@inject OrderService OrderService

<RadzenAlert AlertStyle="AlertStyle.Warning">
    Some orders require attention.
</RadzenAlert>

<RadzenDataGrid Data="@orders" TItem="Order">
    <Columns>
        <RadzenDataGridColumn TItem="Order" Property="Name" Title="Name" />
    </Columns>
</RadzenDataGrid>

<RadzenButton Text="Save" Click="@SaveOrders" />

@code {
    private List<Order> orders = new();

    private async Task SaveAsync()
    {
        await OrderService.SaveOrdersAsync(orders);
    }
}
"""


def _file(path: str = "Components/Orders.razor") -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/repo/{path}",
        language="razor",
        size_bytes=len(_COMPONENT),
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


@pytest.fixture(scope="module")
def parsed(parser: ASTParser):
    return parser.parse_file(_file(), _COMPONENT)


class TestSymbols:
    def test_the_file_itself_becomes_a_component_symbol(self, parsed) -> None:
        # Nothing in a .razor source names the component; the filename does.
        component = [s for s in parsed.symbols if s.name == "Orders"]
        assert len(component) == 1
        assert component[0].kind == "class"
        assert component[0].start_line == 1

    def test_no_parse_errors_on_a_well_formed_component(self, parsed) -> None:
        assert parsed.parse_errors == []


class TestCalls:
    def test_code_block_method_calls_are_extracted(self, parsed) -> None:
        # `OrderService.SaveOrdersAsync(orders)` lives in the @code block,
        # which is projected into C#, so the call edge must survive.
        assert "SaveOrdersAsync" in {c.target_name for c in parsed.calls}

    def test_markup_component_tags_become_calls(self, parsed) -> None:
        # <RadzenDataGrid /> etc. are how Razor instantiates components,
        # the JSX analogue, minted from the markup by component_call_sites.
        targets = {c.target_name for c in parsed.calls}
        assert {
            "RadzenAlert",
            "RadzenDataGrid",
            "RadzenDataGridColumn",
            "RadzenButton",
        } <= targets

    def test_generic_type_arguments_are_not_component_calls(self, parsed) -> None:
        # ``List<Order>`` inside @code is a generic type argument. Treating
        # it as markup would mint a bogus Order edge.
        targets = {c.target_name for c in parsed.calls}
        assert "Order" not in targets
        assert "List" not in targets

    def test_calls_are_attributed_to_the_component(self, parsed) -> None:
        grid = next(c for c in parsed.calls if c.target_name == "RadzenDataGrid")
        assert grid.caller_symbol_id is not None
        assert grid.caller_symbol_id.endswith("::Orders")

    @pytest.mark.xfail(
        strict=True,
        reason="attribute-bound handlers (Click=\"@SaveOrders\") are not projected yet",
    )
    def test_attribute_bound_handler_carries_an_edge(self, parsed) -> None:
        # ``Click="@SaveOrders"`` is the only reference to SaveOrders in the
        # component. Until attribute expressions are projected it reads as
        # unreferenced; this turns green when they are.
        assert "SaveOrders" in {c.target_name for c in parsed.calls}


class TestCshtml:
    def test_cshtml_files_parse_as_razor(self, parser) -> None:
        src = b"""@{
    ViewData["Title"] = "Orders";
    var filtered = orders.Where(o => o.IsActive);
}

@functions {
    public int Count;
}

<div>@filtered.Count() orders</div>
"""
        file_info = _file("Views/Orders.cshtml")
        result = parser.parse_file(file_info, src)
        assert result.parse_errors == []
        # The component symbol comes from the filename, not the source.
        assert "Orders" in {s.name for s in result.symbols}
        # .Where lives inside the @{ } statement block, which is projected
        # into C#, so the call site must survive.
        assert "Where" in {c.target_name for c in result.calls}
        # The @functions body is class content at C# top level (there is no
        # class wrapper yet), so `Count` carries no symbol today, and
        # `@filtered.Count()` is a single expression in markup, which is not
        # projected either.
        assert "Count" not in {s.name for s in result.symbols}

    def test_mvc_view_directives_are_blanked_and_mint_no_edge(self, parser) -> None:
        # ``@model``, ``@Html.Partial`` and the awaited partial / view
        # component forms are single expressions in markup, not code
        # blocks. They are blanked like ``@inject``: no symbol, no edge.
        src = b"""@model OrderDetailViewModel
@using Microsoft.AspNetCore.Mvc.Localization

<h1>@Model.Title</h1>
@Html.Partial("_OrderSummary", Model.Summary)
@await Html.PartialAsync("_OrderLines", Model.Lines)
@await Component.InvokeAsync("Basket")

@{
    var summary = OrderFormatter.Summarise(Model.Lines);
}
"""
        result = parser.parse_file(_file("Views/Order/Detail.cshtml"), src)
        targets = {c.target_name for c in result.calls}
        assert "Partial" not in targets
        assert "PartialAsync" not in targets
        assert "InvokeAsync" not in targets
        assert "OrderDetailViewModel" not in targets
        assert {s.name for s in result.symbols} == {"Detail"}
        # The statement block still projects.
        assert "Summarise" in targets


class TestHealth:
    def test_complexity_walker_reads_the_projection(self) -> None:
        from repowise.core.analysis.health.complexity.walker import walk_file

        src = b"""@page "/orders"

<RadzenDataGrid Data="@orders" TItem="Order">
</RadzenDataGrid>

@code {
    private int count;

    private async Task SaveAsync()
    {
        for (var i = 0; i < 10; i++)
        {
            await OrderService.SaveOrdersAsync(orders);
        }
    }
}
"""
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".razor", delete=False) as f:
            f.write(src)
            path = f.name
        try:
            fc = walk_file(path, "razor", src)
            names = {fn.name for fn in fc.functions}
            assert "SaveAsync" in names
            assert fc.file_nloc > 0
        finally:
            os.unlink(path)

    def test_perf_dialect_is_registered(self) -> None:
        # Razor reaches the perf pass as a C# buffer, so the C# dialect
        # serves it, the same alias pattern as svelte -> ts_js.
        from repowise.core.analysis.health.perf.dialects import PERF_DIALECTS

        assert "razor" in PERF_DIALECTS
