pub struct Cart {
    items: Vec<u32>,
}

impl Cart {
    pub fn new() -> Self {
        Cart { items: Vec::new() }
    }

    pub fn add(&mut self, item: u32) {
        self.items.push(item);
    }

    pub fn total(&self) -> u32 {
        let calc = |items: &[u32]| -> u32 {
            let mut sum = 0;
            for &item in items {
                sum += item;
            }
            sum
        };
        calc(&self.items)
    }
}

pub fn helper() -> u32 {
    42
}
