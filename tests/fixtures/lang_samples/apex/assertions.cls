// Fixtures for Apex assertion-block detection and test-case recognition.
@IsTest
private class Assertions {
    @IsTest
    static void testManyAsserts() {
        Integer x = compute();
        System.assertEquals(1, x);
        System.assertNotEquals(2, x);
        Assert.areEqual(1, x);
        Assert.isTrue(x > 0);
        System.assert(x != 3, 'not three');
    }

    @isTest
    static void testFewAsserts() {
        Integer x = compute();
        Assert.areEqual(1, x);
        Integer y = x + 1;
        Assert.areEqual(2, y);
    }

    static testMethod void legacyCase() {
        Integer x = compute();
        System.assertEquals(1, x);
    }

    @IsTest
    static void checksNothing() {
        compute();
    }

    @TestSetup
    static void makeData() {
        insert new Account(Name = 'x');
    }

    private static Integer compute() {
        return 1;
    }
}
