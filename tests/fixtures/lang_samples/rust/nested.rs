fn deeply_nested(x: i32) -> i32 {
    if x > 0 {
        for i in 0..x {
            if i % 2 == 0 {
                let mut j = i;
                while j > 0 {
                    if j == 5 {
                        return j;
                    }
                    j -= 1;
                }
            }
        }
    }
    0
}

fn shallow(x: i32) -> i32 {
    x + 1
}
