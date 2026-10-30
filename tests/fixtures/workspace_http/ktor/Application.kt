package com.example

import io.ktor.resources.Resource
import io.ktor.server.application.Application
import io.ktor.server.resources.get
import io.ktor.server.response.respondText
import io.ktor.server.routing.Route
import io.ktor.server.routing.delete
import io.ktor.server.routing.get
import io.ktor.server.routing.post
import io.ktor.server.routing.route
import io.ktor.server.routing.routing

@Resource("/articles")
class Articles

fun Application.module() {
    routing {
        get {
            call.respondText("home")
        }
        route("/api") {
            route("/users") {
                get {
                    call.respondText("all users")
                }
                get("{id}") {
                    call.respondText("one user")
                }
                post {
                    call.respondText("created")
                }
            }
            get("/status") {
                call.respondText("ok")
            }
        }
        get<Articles> {
            call.respondText("typed")
        }
        customerRoutes()
    }
}

fun Route.customerRoutes() {
    route("/customers") {
        delete("{id}") {
            call.respondText("deleted")
        }
    }
    // The prefix is wherever customerRoutes() is called.
    get {
        call.respondText("unknown prefix")
    }
}
