// Fixtures for the Apex health markers: the class declares no sharing keyword
// and runs SOQL (apex_missing_sharing); each method below either fires one
// marker or shows a shape that must stay quiet.
public class HealthMarkers {
    private static final String BASE = 'SELECT Id FROM Account';
    private static final Id OWNER = '0051t000002bF3QAAU';
    public String searchText { get; set; }
    private String internalName;

    // -- fire ------------------------------------------------------------------

    public List<Account> byName(String name) {
        return Database.query(BASE + ' WHERE Name = \'' + name + '\'');
    }

    public List<Account> byBuiltClause(String city) {
        String soql = BASE;
        String clause = ' WHERE BillingCity LIKE \'%' + city.trim() + '%\'';
        soql += clause;
        return Database.query(soql);
    }

    public List<List<SObject>> bySearchProperty() {
        return Search.query('FIND \'' + searchText + '\' IN ALL FIELDS RETURNING Account');
    }

    public List<Account> byNames(List<String> names) {
        return Database.query(BASE + ' WHERE Name IN (\'' + String.join(names, '\',\'') + '\')');
    }

    public Id defaultRecordType() {
        return '012000000000AbcAAE';
    }

    // -- stay quiet ------------------------------------------------------------

    public List<Account> escaped(String name) {
        return Database.query(BASE + ' WHERE Name = \'' + String.escapeSingleQuotes(name) + '\'');
    }

    public List<Account> escapedFirst(String name) {
        name = String.escapeSingleQuotes(name);
        return Database.query(BASE + ' WHERE Name = \'' + name + '\'');
    }

    public List<Account> bound(String name) {
        return Database.query(BASE + ' WHERE Name = :name');
    }

    public List<Account> typedId(Id recordId) {
        return Database.query(BASE + ' WHERE Id = \'' + recordId + '\'');
    }

    public List<SObject> fieldList(List<String> fields, String objectName) {
        // Identifiers outside quotes: escaping cannot protect them, not flagged.
        return Database.query('SELECT ' + String.join(fields, ',') + ' FROM ' + objectName);
    }

    public List<Account> privateField() {
        return Database.query(BASE + ' WHERE Name = \'' + internalName + '\'');
    }

    public List<Account> inline() {
        return [SELECT Id FROM Account WHERE OwnerId = :OWNER];
    }

    public void notIds() {
        String word = 'AccountServices';
        String digits = '001000000000000';
        String sample = '001000000000000AAA';
    }

    public with sharing class Inner {
        public void touch(List<Account> rows) {
            update rows;
        }
    }
}
