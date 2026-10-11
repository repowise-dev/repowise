package com.sample.app.model;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import lombok.Data;

@Entity
@Data
public class User {

    @Id
    private Long id;
    private String email;
    private String status;

    public static User empty() {
        return new User();
    }
}
