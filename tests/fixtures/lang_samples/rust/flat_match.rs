// Flat match: all arms are simple single-expression arms.
// Should count as 1 CCN point for the match keyword, NOT per-arm.
fn flat_match(x: i32) -> &'static str {
    match x {
        1 => "one",
        2 => "two",
        3 => "three",
        _ => "other",
    }
}

// Complex match: at least one arm has nested control flow.
// Each arm should count as a branch point (existing behavior).
fn complex_match(x: i32) -> i32 {
    match x {
        1 => {
            if x > 0 {
                42
            } else {
                0
            }
        }
        2 => 100,
        _ => 0,
    }
}

// Mixed: one arm has a block with multiple statements.
fn multi_stmt_match(x: i32) -> i32 {
    match x {
        1 => {
            let a = 10;
            a + x
        }
        2 => 20,
        _ => 0,
    }
}
