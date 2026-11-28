package com.shop.orders;

import com.acme.billing.client.BillingClient;
import com.acme.billing.client.Invoice;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class OrderApi {
    private final BillingClient billing;

    public OrderApi(BillingClient billing) {
        this.billing = billing;
    }

    @PostMapping("/orders")
    public Invoice bill(String accountId) {
        return billing.charge(accountId, 100L);
    }
}
