package com.acme.billing.client;

public class Invoice {
    private final String accountId;
    private final long cents;

    public Invoice(String accountId, long cents) {
        this.accountId = accountId;
        this.cents = cents;
    }

    public long cents() {
        return cents;
    }
}
