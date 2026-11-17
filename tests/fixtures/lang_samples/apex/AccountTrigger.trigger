trigger AccountTrigger on Account (before insert, after update) {
    AccountTriggerHandler.run(Trigger.new);
    new AccountService().execute(null);
}
