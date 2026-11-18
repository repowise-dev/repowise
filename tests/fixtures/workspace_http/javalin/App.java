package com.example;

import static io.javalin.apibuilder.ApiBuilder.delete;
import static io.javalin.apibuilder.ApiBuilder.get;
import static io.javalin.apibuilder.ApiBuilder.path;
import static io.javalin.apibuilder.ApiBuilder.post;

import io.javalin.Javalin;
import java.util.HashMap;
import java.util.Map;

public class App {
    public static void main(String[] args) {
        Javalin app = Javalin.create(config -> {
            config.router.apiBuilder(() -> {
                path("/api", () -> {
                    path("users", () -> {
                        get(UserController::getAll);
                        post(UserController::create);
                        path("{id}", () -> {
                            get(UserController::getOne);
                            delete(ctx -> UserController.delete(ctx));
                        });
                    });
                });
                get("/version", ctx -> ctx.result("1.0"));
            });
        })
            .get("/health", ctx -> ctx.result("ok"))
            .start(7070);

        app.post("/orders", ctx -> ctx.status(201));
        app.get("/orders/{id}", OrderController::getOne);

        Map<String, String> cache = new HashMap<>();
        cache.get("/orders/cached");
    }

    public String get(String key) {
        return key;
    }
}
