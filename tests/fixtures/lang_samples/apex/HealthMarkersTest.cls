// Fixtures for apex_see_all_data: the class and one method read live org data.
@IsTest(SeeAllData=true)
private class HealthMarkersTest {
    @isTest(seeAllData = true)
    static void readsOrgData() {
        Id fixtureId = '0011t00000ABCDeAAP';
        System.assertNotEquals(null, fixtureId);
    }

    @IsTest(SeeAllData=false)
    static void ownData() {
        insert new Account(Name = 'x');
    }

    @IsTest(SeeAllData=true)
    @SuppressWarnings('PMD.ApexUnitTestShouldNotUseSeeAllDataTrue')
    static void suppressed() {
    }
}
