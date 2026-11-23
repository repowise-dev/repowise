import pytest

from repowise.core.workspace.extractors.http.express import _APP_USE_RE


def _target(src):
    m = _APP_USE_RE.search(src)
    return m.group(2) if m else None


@pytest.mark.parametrize(
    "src, expected",
    [
        ('app.use("/api", require("./r"));', 'require("./r")'),
        ('router.use("/v1", child.routes());', "child"),
        ('app.use("/users", users.router);', "users.router"),
        ('app.use("/p", router);', "router"),
        ('app.use("/p", a.b);', "a.b"),
        ('app.use("/p", makeRouter());', None),
    ],
)
def test_express_mount_target(src, expected):
    assert _target(src) == expected
