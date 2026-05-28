package com.sample.app.svc

import com.sample.app.model.User
import org.springframework.stereotype.Service

data class UserDto(val id: Long, val email: String, val status: String)

@Service
class UserService(private val repository: UserRepository) {

    fun findById(id: Long): User {
        return repository.findByEmailAndStatus("user@example.com", "ACTIVE")
            .firstOrNull()
            ?: User.empty()
    }

    companion object {
        fun defaultStatus(): String = "ACTIVE"
    }
}
