// Fixture for assertion-block detection (test-quality smells).
// GoogleTest-style EXPECT_*/ASSERT_* macros are ordinary call expressions.

void testManyAsserts() {
    EXPECT_EQ(1, 1);
    EXPECT_EQ(1, 1);
    EXPECT_EQ(1, 1);
    EXPECT_EQ(1, 1);
    EXPECT_EQ(1, 1);
    EXPECT_EQ(1, 1);
    EXPECT_EQ(1, 1);
    EXPECT_EQ(1, 1);
    EXPECT_EQ(1, 1);
    EXPECT_EQ(1, 1);
    EXPECT_EQ(1, 1);
    EXPECT_EQ(1, 1);
    EXPECT_EQ(1, 1);
    EXPECT_EQ(1, 1);
    EXPECT_EQ(1, 1);
    EXPECT_EQ(1, 1);
}

void testFewAsserts() {
    EXPECT_EQ(1, 1);
    int x = 2;
    EXPECT_EQ(x, 2);
}
