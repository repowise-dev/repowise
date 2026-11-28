package com.acme.billing.client;

import org.springframework.web.client.RestTemplate;

public class BillingClient {
    private final RestTemplate rest = new RestTemplate();

    public Invoice charge(String accountId, long cents) {
        return rest.postForObject("/charges", accountId, Invoice.class);
    }
}
