package com.acme.billing.service;

import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class ChargeController {
    @PostMapping("/charges")
    public String create(String accountId) {
        return accountId;
    }
}
