"""Which Java classes and repository files a Java test replaces outright with doubles."""

from __future__ import annotations

import pytest

from repowise.core.analysis.test_reach import replaced_files
from repowise.core.analysis.test_reach.mocks_java import (
    replaced_java_classes,
    replaced_java_files,
)

_T = "src/test/java/com/acme/service/OrderServiceTest.java"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            """
            package com.acme.service;
            import org.mockito.Mock;
            import com.acme.client.PriceClient;
            class OrderServiceTest {
                @Mock
                PriceClient prices;
            }
            """,
            {"PriceClient"},
        ),
        (
            """
            package com.acme.service;
            import org.springframework.boot.test.mock.mockito.MockBean;
            import com.acme.client.PriceClient;
            class OrderServiceTest {
                @MockBean
                PriceClient prices;
            }
            """,
            {"PriceClient"},
        ),
        (
            """
            package com.acme.service;
            import org.springframework.test.context.bean.override.mockito.MockitoBean;
            import com.acme.client.PriceClient;
            class OrderServiceTest {
                @MockitoBean
                PriceClient prices;
            }
            """,
            {"PriceClient"},
        ),
        (
            """
            package com.acme.service;
            import static org.mockito.Mockito.mock;
            import com.acme.client.PriceClient;
            class OrderServiceTest {
                void test() {
                    PriceClient p = mock(PriceClient.class);
                }
            }
            """,
            {"PriceClient"},
        ),
        (
            """
            package com.acme.service;
            import org.mockito.Mockito;
            import com.acme.client.PriceClient;
            class OrderServiceTest {
                void test() {
                    PriceClient p = Mockito.mock(PriceClient.class);
                }
            }
            """,
            {"PriceClient"},
        ),
        (
            """
            package com.acme.service;
            import com.acme.client.PriceClient;
            class OrderServiceTest {
                void test() {
                    PriceClient p = org.mockito.Mockito.mock(PriceClient.class);
                }
            }
            """,
            {"PriceClient"},
        ),
        (
            """
            package com.acme.service;
            import org.mockito.Mock;
            import com.acme.client.PriceClient;
            class OrderServiceTest {
                @Test
                void testMethod(@Mock PriceClient p) {}
            }
            """,
            {"PriceClient"},
        ),
        (
            """
            package com.acme.service;
            import org.mockito.Mock;
            import com.acme.client.PriceClient;
            class OrderServiceTest {
                @Mock
                PriceClient<String> prices;
            }
            """,
            {"PriceClient"},
        ),
        (
            """
            package com.acme.service;
            import org.mockito.Mock;
            class OrderServiceTest {
                @Mock
                com.acme.client.PriceClient prices;
            }
            """,
            {"com.acme.client.PriceClient"},
        ),
    ],
)
def test_a_mock_replaces_the_class(source: str, expected: set[str]) -> None:
    assert replaced_java_classes(_T, source) == expected


@pytest.mark.parametrize(
    "source",
    [
        # @Spy
        """
        package com.acme.service;
        import org.mockito.Spy;
        import com.acme.client.PriceClient;
        class OrderServiceTest {
            @Spy
            PriceClient prices;
        }
        """,
        # spy(...)
        """
        package com.acme.service;
        import static org.mockito.Mockito.spy;
        import com.acme.client.PriceClient;
        class OrderServiceTest {
            void test() {
                PriceClient p = spy(PriceClient.class);
            }
        }
        """,
        # @InjectMocks
        """
        package com.acme.service;
        import org.mockito.InjectMocks;
        import com.acme.client.PriceClient;
        class OrderServiceTest {
            @InjectMocks
            PriceClient prices;
        }
        """,
        # Instantiation via new Foo()
        """
        package com.acme.service;
        import org.mockito.Mock;
        import com.acme.client.PriceClient;
        class OrderServiceTest {
            @Mock
            PriceClient prices;
            void test() {
                new PriceClient();
            }
        }
        """,
        # CALLS_REAL_METHODS
        """
        package com.acme.service;
        import org.mockito.Mock;
        import static org.mockito.Mockito.CALLS_REAL_METHODS;
        import com.acme.client.PriceClient;
        class OrderServiceTest {
            @Mock
            PriceClient prices;
            void test() {
                mock(PriceClient.class, CALLS_REAL_METHODS);
            }
        }
        """,
        # thenCallRealMethod()
        """
        package com.acme.service;
        import org.mockito.Mock;
        import com.acme.client.PriceClient;
        class OrderServiceTest {
            @Mock
            PriceClient prices;
            void test() {
                when(prices.get()).thenCallRealMethod();
            }
        }
        """,
        # doCallRealMethod()
        """
        package com.acme.service;
        import org.mockito.Mock;
        import com.acme.client.PriceClient;
        class OrderServiceTest {
            @Mock
            PriceClient prices;
            void test() {
                doCallRealMethod().when(prices).get();
            }
        }
        """,
        # Not a Java file
        """
        import org.mockito.Mock;
        class OrderServiceTest {}
        """,
    ],
)
def test_anything_unproven_swaps_nothing(source: str) -> None:
    if "Not a Java file" in source:
        assert replaced_java_classes("src/OrderServiceTest.py", source) == frozenset()
    else:
        assert replaced_java_classes(_T, source) == frozenset()


def test_syntax_error_abstains() -> None:
    assert replaced_java_classes(_T, "class OrderServiceTest { @Mock PriceClient") == frozenset()


def test_java_mock_masks_matching_repo_file() -> None:
    files = {
        "src/main/java/com/acme/client/PriceClient.java",
        "src/main/java/com/acme/service/OrderService.java",
        "src/main/java/com/acme/model/ItemModel.java",
        _T,
    }
    texts = {
        _T: """
        package com.acme.service;
        import com.acme.client.PriceClient;
        import com.acme.model.*;
        import org.mockito.Mock;
        import org.mockito.Spy;
        import org.mockito.InjectMocks;
        import org.springframework.boot.test.mock.mockito.MockBean;

        class OrderServiceTest {
            @Mock
            PriceClient prices;

            @MockBean
            ItemModel items;

            @Mock
            OrderService helper;

            @InjectMocks
            OrderService service;

            @Spy
            PriceClient spyPrices;
        }
        """,
    }
    # PriceClient has @Spy, so excluded.
    # OrderService has @InjectMocks, so excluded.
    # ItemModel is matched via wildcard com.acme.model.*.
    assert replaced_java_files(_T, texts[_T], files) == {
        "src/main/java/com/acme/model/ItemModel.java"
    }


def test_same_package_mock_masks_file() -> None:
    files = {
        "src/main/java/com/acme/service/OrderValidator.java",
        _T,
    }
    text = """
    package com.acme.service;
    import org.mockito.Mock;

    class OrderServiceTest {
        @Mock
        OrderValidator validator;
    }
    """
    assert replaced_java_files(_T, text, files) == {
        "src/main/java/com/acme/service/OrderValidator.java"
    }


def test_ambiguous_matches_abstain() -> None:
    files = {
        "mod-a/src/main/java/com/acme/service/OrderValidator.java",
        "mod-b/src/main/java/com/acme/service/OrderValidator.java",
        _T,
    }
    text = """
    package com.acme.service;
    import org.mockito.Mock;

    class OrderServiceTest {
        @Mock
        OrderValidator validator;
    }
    """
    assert replaced_java_files(_T, text, files) == frozenset()


def test_third_party_class_masks_nothing() -> None:
    files = {_T}
    text = """
    package com.acme.service;
    import org.mockito.Mock;
    import java.util.List;

    class OrderServiceTest {
        @Mock
        List<String> list;
    }
    """
    assert replaced_java_files(_T, text, files) == frozenset()


def test_replaced_files_integration() -> None:
    files = {
        "src/main/java/com/acme/client/PriceClient.java",
        _T,
    }
    texts = {
        _T: """
        package com.acme.service;
        import com.acme.client.PriceClient;
        import org.mockito.Mock;

        class OrderServiceTest {
            @Mock
            PriceClient prices;
        }
        """
    }
    assert replaced_files(texts, files) == {
        _T: frozenset({"src/main/java/com/acme/client/PriceClient.java"}),
    }
