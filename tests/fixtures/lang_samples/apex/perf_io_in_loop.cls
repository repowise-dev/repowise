// Fixtures for the Apex perf dialect: operations with governor limits inside
// a loop fire io_in_loop; the bulk idioms and run-once shapes stay quiet.
public with sharing class PerfIoInLoop {
    // -- fire ------------------------------------------------------------------

    public void soqlPerRecord(List<Contact> contacts) {
        for (Contact c : contacts) {
            Account a = [SELECT Id FROM Account WHERE Id = :c.AccountId];
        }
    }

    public void dmlPerRecord(List<Account> accounts) {
        for (Account a : accounts) {
            update a;
        }
    }

    public void databaseAndCallout(List<Id> ids, HttpRequest req) {
        Http http = new Http();
        for (Id i : ids) {
            Database.delete(i);
            HttpResponse res = http.send(req);
        }
    }

    public void asyncPerRecord(List<Id> ids) {
        for (Id i : ids) {
            System.enqueueJob(new Worker(i));
            notifyLater(i);
        }
    }

    @future
    private static void notifyLater(Id i) {
    }

    public void nested(List<Account> accounts) {
        for (Account a : accounts) {
            for (Contact c : a.Contacts) {
                insert new Task(WhoId = c.Id);
            }
        }
    }

    // -- stay quiet --------------------------------------------------------------

    public void bulk(List<Account> accounts) {
        List<Account> changed = new List<Account>();
        for (Account a : accounts) {
            changed.add(a);
        }
        update changed;
        List<Contact> rows = [SELECT Id FROM Contact WHERE AccountId IN :accounts];
    }

    public void queryInHeader() {
        for (Account a : [SELECT Id FROM Account]) {
            a.Name = 'x';
        }
    }

    public void soqlForChunks() {
        for (List<Account> chunk : [SELECT Id FROM Account]) {
            update chunk;
        }
    }

    public void literalBound(Account a) {
        for (Integer i = 0; i < 3; i++) {
            insert new Task(WhatId = a.Id);
        }
        for (String s : new List<String>{ 'a', 'b' }) {
            insert new Task(Subject = s);
        }
    }

    public void chunked(List<Account> rows, Integer size) {
        for (Integer i = 0; i < rows.size(); i += size) {
            Database.update(rows, false);
        }
    }

    public void flushAtSize(List<Account> rows) {
        List<Account> buffer = new List<Account>();
        for (Account a : rows) {
            buffer.add(a);
            if (buffer.size() == 100) {
                insert buffer;
                buffer.clear();
            }
        }
    }

    public void cached(List<Account> rows) {
        Map<Id, Account> byId;
        for (Account a : rows) {
            if (byId == null) {
                byId = new Map<Id, Account>([SELECT Id FROM Account]);
            }
        }
    }

    public void firstMatch(List<Account> rows) {
        for (Account a : rows) {
            if (a.Name == 'x') {
                update a;
                break;
            }
        }
    }

    public void perObjectType(Map<String, Set<Id>> idsByType) {
        for (String objectName : idsByType.keySet()) {
            Set<Id> ids = idsByType.get(objectName);
            List<SObject> recs = Database.query('SELECT Id FROM ' + objectName + ' WHERE Id IN :ids');
        }
    }

    public void perGroup(Map<String, List<SObject>> byType) {
        for (String t : byType.keySet()) {
            insert byType.get(t);
        }
    }

    public void customMetadata(List<String> names) {
        for (String n : names) {
            List<Setting__mdt> s = [SELECT Id FROM Setting__mdt WHERE DeveloperName = :n];
        }
    }

    public void mergeAll(Account master, List<Account> dupes) {
        for (Account d : dupes) {
            merge master d;
        }
    }

    // Ceiling: a hierarchy walk in a `while` is not reported.
    public void cursor(Id startId) {
        Id current = startId;
        while (current != null) {
            current = [SELECT ParentId FROM Account WHERE Id = :current].ParentId;
        }
    }
}
