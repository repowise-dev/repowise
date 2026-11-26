<?php

namespace Shop;

class Cart {
    /** @var array<string, int> */
    private array $items = [];

    public function __construct() {
        $this->items = [];
    }

    public static function empty(): self {
        return new self();
    }

    public function add(string $item, int $price): void {
        $this->items[$item] = $price;
    }

    public function total(): int {
        $calc = function (array $items): int {
            $sum = 0;
            foreach ($items as $price) {
                $sum += $price;
            }
            return $sum;
        };
        return $calc($this->items);
    }
}

$cart = Cart::empty();
$cart->add("apple", 100);
$cart->add("banana", 200);
$total = $cart->total();
