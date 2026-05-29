// Fixture for assertion-block detection (test-quality smells).
// xUnit-style Assert.* invocations.

class Tests
{
    void TestManyAsserts()
    {
        Assert.Equal(1, 1);
        Assert.Equal(1, 1);
        Assert.Equal(1, 1);
        Assert.Equal(1, 1);
        Assert.Equal(1, 1);
        Assert.Equal(1, 1);
        Assert.Equal(1, 1);
        Assert.Equal(1, 1);
        Assert.Equal(1, 1);
        Assert.Equal(1, 1);
        Assert.Equal(1, 1);
        Assert.Equal(1, 1);
        Assert.Equal(1, 1);
        Assert.Equal(1, 1);
        Assert.Equal(1, 1);
        Assert.Equal(1, 1);
    }

    void TestFewAsserts()
    {
        Assert.Equal(1, 1);
        int x = 2;
        Assert.Equal(x, 2);
    }
}
