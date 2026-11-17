package com.example

import io.ktor.client.request.get
import io.ktor.server.routing.get
import io.ktor.server.routing.routing
import io.ktor.server.testing.testApplication

class ApplicationTest {
    fun testRoot() = testApplication {
        routing {
            get("/test-only") {
                call.respondText("test route")
            }
        }
        val response = client.get("/api/users")
    }
}
