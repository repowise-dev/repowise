package com.example

import io.javalin.Javalin
import io.javalin.apibuilder.ApiBuilder.get
import io.javalin.apibuilder.ApiBuilder.path

fun main() {
    val app = Javalin.create { config ->
        config.router.apiBuilder {
            path("/kt") {
                get { ctx -> ctx.result("root") }
                get("/items") { ctx -> ctx.result("items") }
            }
        }
    }.start(7070)
    app.put("/kt/items/{id}") { ctx -> ctx.status(204) }
}
