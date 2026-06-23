// Fixture for the Rust performance dialect (io_in_loop / regex_compile_in_loop /
// resource_construction_in_loop / blocking_sync_in_async). Every POSITIVE is a
// genuine per-iteration cost; every NEGATIVE is a shape the dialect must NOT
// flag. Hand-counted expectations live in test_perf_dialects.py.
//
// NOTE: Rust `String` building (`push_str` / `+=`) is amortized O(1), so
// `string_concat_in_loop` is intentionally not a Rust marker — the push_str
// loop below is a NEGATIVE by design.

use std::fs;

// --- POSITIVES -------------------------------------------------------------

async fn sqlx_fetch_in_loop(pool: &PgPool, ids: Vec<i32>) {
    for id in ids {
        // POSITIVE: a sqlx executor verb (db round-trip) per item, awaited
        // (also a serial_await_in_loop co-signal).
        let _ = sqlx::query("SELECT 1").fetch_all(pool).await;
    }
}

fn fs_read_in_loop(paths: Vec<String>) {
    for p in paths {
        let _ = std::fs::read(&p); // POSITIVE: filesystem per item
    }
}

fn fs_read_to_string_in_loop(paths: Vec<String>) {
    for p in paths {
        let _ = fs::read_to_string(&p); // POSITIVE: filesystem (short `fs::` path)
    }
}

async fn reqwest_get_in_loop(urls: Vec<String>) {
    for u in urls {
        // POSITIVE: network per item, awaited (serial_await co-signal).
        let _ = reqwest::get(&u).await;
    }
}

fn regex_new_in_loop(items: Vec<String>) {
    for x in items {
        let re = Regex::new("^[a-z]+$"); // POSITIVE: static literal pattern, hoistable
        let dyn_re = Regex::new(&x); // NEGATIVE: dynamic arg cannot be hoisted
        let _ = (re, dyn_re);
    }
}

async fn pgpool_connect_in_loop(urls: Vec<String>) {
    for u in urls {
        // POSITIVE: a fresh pooled connection per item (resource construction).
        let _ = PgPool::connect(&u).await;
    }
}

async fn thread_sleep_in_async(d: std::time::Duration) {
    std::thread::sleep(d); // POSITIVE: blocking the executor inside an async fn
}

// --- NEGATIVES -------------------------------------------------------------

fn fs_read_constant_loop() {
    for _ in 0..10 {
        let _ = std::fs::read("x"); // NEGATIVE: bounded integer-literal range
    }
}

fn fs_read_outside_loop(p: String) {
    let _ = std::fs::read(&p); // NEGATIVE: runs once, not in a loop
}

fn push_str_in_loop(items: Vec<String>) -> String {
    let mut s = String::new();
    for x in items {
        s.push_str(&x); // NEGATIVE: amortized O(1) — not a Rust perf smell
    }
    s
}
