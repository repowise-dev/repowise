// Fixtures for class-level (LCOM4 / god-class) walker tests.
//
// Uses explicit `this->` so the member-access node type is exercised; bare
// member access (idiomatic C++) is the documented "no signal" path.

class Cohesive {
    int total;
    int count;

public:
    void add(int n) {
        this->total += n;
        this->count += 1;
    }

    int average() {
        return this->count ? this->total / this->count : 0;
    }

    void reset() {
        this->total = 0;
        this->count = 0;
    }

    int describe() {
        return this->count;
    }
};

class Splintered {
    int a;
    int b;

public:
    void setA(int v) {
        this->a = v;
    }

    int getA() {
        return this->a;
    }

    void setB(int v) {
        this->b = v;
    }

    int getB() {
        return this->b;
    }

    int loner() {
        return 42;
    }
};
