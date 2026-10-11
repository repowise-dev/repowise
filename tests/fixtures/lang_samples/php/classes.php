<?php
// Class-metrics fixture: LCOM4 and class-level properties for PHP.

class CohesiveClass {
    private int $total = 0;

    public function add(int $x): void {
        $this->total += $x;
    }

    public function getTotal(): int {
        return $this->total;
    }

    public function reset(): void {
        $this->total = 0;
        $this->helper();
    }

    private function helper(): void {
        // internal helper
    }
}

class SplitClass {
    private int $alpha = 0;
    private int $beta = 0;

    public function stepAlpha(): void {
        $this->alpha += 1;
    }

    public function getAlpha(): int {
        return $this->alpha;
    }

    public function stepBeta(): void {
        $this->beta += 2;
    }

    public function getBeta(): int {
        return $this->beta;
    }
}

trait LoggableTrait {
    private string $prefix = "[LOG]";

    public function log(string $msg): void {
        echo $this->prefix . ": " . $msg;
    }

    public function setPrefix(string $p): void {
        $this->prefix = $p;
    }
}

interface WorkerInterface {
    public function performWork(): void;
}

enum Priority {
    case Low;
    case High;
}
