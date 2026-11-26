package com.shop.orders;

import com.acme.billing.client.BillingClient;
import com.acme.billing.client.Invoice;

public class OrderApi {
    private final BillingClient billing;

    public OrderApi(BillingClient billing) {
        this.billing = billing;
    }

    public Invoice bill(String accountId) {
        return billing.charge(accountId, 100L);
    }
}
