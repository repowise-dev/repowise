from repowise.core.analysis.health.complexity.walker import walk_file


def param_counts(src: str) -> dict[str, int]:
    """Run the analyzer on F# code and return {function name: param_count}."""
    result = walk_file("demo.fs", "fsharp", src.encode())
    return {fn.name: fn.param_count for fn in result.functions}










def test_let_curried_space_separated_parameters_counts_each_one():
    src = "let getCookies uri responseUri (headers: Map<string, string>) = uri"
    assert param_counts(src)["getCookies"] == 3


def test_let_curried_and_nested_tupled_parameters_count_tuple_as_one_curried_parameter():
    src = "let functionName curriedParam1 (tupledParam1, tupledParam2 : int, (nestedTupled1, nestedTupled2)) (curriedParam2: int) (tupledParam3) curriedParam3 x = 2026"
    assert param_counts(src)["functionName"] == 6


def test_let_tupled_parameters():
    src = "let functionName (tupledParam1, tupledParam2 : int, tupledParam3, tupledParam4) = 2026"
    assert param_counts(src)["functionName"] == 1


def test_let_single_tupled_parameter():
    src = "let functionName (tupledParam1) = 2026"
    assert param_counts(src)["functionName"] == 1



def test_unit_functions_returns_zero():
    src = "let getCookies () = 2026"    # functions with zero parameters
    assert param_counts(src)["getCookies"] == 0


def test_value_binding_has_no_parameters():
    src = "let x = 5"
    assert "x" not in param_counts(src)  # to check that value binding is not considered as a function due to 'let' keyword











def test_member_curried_params_with_tuples():
    src = """type T() =
    member x.Add (b, c: int) a (e, f: int) = a + b + c
"""
    assert param_counts(src)["Add"] == 3



def test_member_curried_flat_params():
    src = """type T() =
    member x.Add a b c: int = a + b + c
"""
    assert param_counts(src)["Add"] == 3



def test_member_curried_single_param():
    src = """type T() =
    member x.Square a: int = a * a
"""
    assert param_counts(src)["Square"] == 1


def test_member_curried_flat_tupled_params():
    src = """type T() =
    member x.Add (a , b, c: int) (e,f,g) = a + b + c + e + f + g
"""
    assert param_counts(src)["Add"] == 2



def test_member_tupled_counts_each_element():
    src = """type T() =
    member x.Add(b, (a, c), e) = a + (b + c) + (d + e)
"""
    assert param_counts(src)["Add"] == 3



def test_member_tupled_redundant_parentheses():
    src = """type T() =
    member x.Subtract((b, e)) = b - a
"""
    assert param_counts(src)["Subtract"] == 1


def test_member_nested_tupled_counts_each_element():
    src = """type T() =
    member x.Add(a: int, (b: int, c, d), e: int, f) = a + (b + c) + (d + e)
"""
    assert param_counts(src)["Add"] == 4



def test_unit_member_has_no_parameters():
    src = """type T() =
    member x.Reset() = 0
"""
    assert param_counts(src)["Reset"] == 0


def test_self_identifier_is_not_a_parameter():
    src = """type T() =
    member x.Cons(c) = c
"""
    assert param_counts(src)["Cons"] == 1
