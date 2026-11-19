import pytest

from repowise.core.workspace.extractors.http.express import _APP_USE_RE


def _key(src):
    m = _APP_USE_RE.search(src)
    return m.group(2).split(".")[-1] if m else None


@pytest.mark.parametrize(
    "src, expected",
    [
        ('app.use("/api", require("./r"));', None),
        ('router.use("/v1", child.routes());', "child"),
        ('app.use("/users", users.router);', "router"),
        ('app.use("/p", router);', "router"),
        ('app.use("/p", a.b);', "b"),
    ],
)
def test_express_mount_key(src, expected):
    assert _key(src) == expected
