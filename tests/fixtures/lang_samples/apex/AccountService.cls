/**
 * Account operations exposed to Lightning.
 */
public with sharing class AccountService extends BaseService implements Queueable, Database.AllowsCallouts {
    public String status { get; set; }
    private static final Integer LIMIT_SIZE = 50;

    public AccountService() {
        this(LIMIT_SIZE);
    }

    public AccountService(Integer size) {
        super(size);
    }

    /** Accounts matching a name. */
    @AuraEnabled(cacheable=true)
    public static List<Account> findByName(String name) {
        AccountSelector selector = new AccountSelector();
        List<Account> rows = selector.byName(name);
        AccountSelector.log(name);
        System.debug(rows.size());
        return [SELECT Id, Name FROM Account WHERE Name = :name LIMIT 50];
    }

    public void execute(QueueableContext context) {
        Account acc = new Account(Name = 'x');
        insert acc;
        this.notify(acc);
        new Result.Builder().build();
        Result.Codes.lookup('A');
    }

    private void notify(Account acc) {
        helper();
    }

    private void helper() {}

    public class Result {
        public Boolean ok { get; private set; }
    }

    public interface Notifier extends Callable {
        void send(String message);
    }

    public enum Mode { SYNC, ASYNC }
}
