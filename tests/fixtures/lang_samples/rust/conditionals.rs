pub fn three_ops(a: bool, b: bool, c: bool, d: bool) -> i32 {
    if a && b && c && d {
        return 1;
    }
    0
}

pub fn six_ops(a: bool, b: bool, c: bool, d: bool, e: bool, f: bool, g: bool) -> i32 {
    while a && b && c && d && e && f && g {
        return 1;
    }
    0
}

pub fn two_ops(a: bool, b: bool, c: bool) -> i32 {
    if a && b || c {
        return 1;
    }
    0
}
