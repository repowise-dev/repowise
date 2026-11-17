package com.example;

import io.javalin.Javalin;

public class V7App {
    public static void main(String[] args) {
        Javalin.create(config -> {
            config.routes.get("/v7/ping", ctx -> ctx.result("pong"));
            config.routes.post("/v7/items", ItemController::create);
        }).start(7070);
    }
}
