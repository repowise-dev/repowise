package com.sample.app.svc;

import com.sample.app.model.User;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface UserRepository extends JpaRepository<User, Long> {

    List<User> findByEmailAndStatus(String email, String status);
}
