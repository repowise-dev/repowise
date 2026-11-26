package com.acme.billing.client;

public class BillingClient {
    public Invoice charge(String accountId, long cents) {
        return new Invoice(accountId, cents);
    }
}
