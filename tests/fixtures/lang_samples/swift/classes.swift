// Fixtures for class-level (LCOM4 / god-class) walker tests.
//
// Swift reaches instance members without a receiver idiomatically; these
// fixtures write `self.` so the member-access node type is exercised.

class Cohesive {
    var total = 0
    var count = 0

    func add(_ n: Int) {
        self.total += n
        self.count += 1
    }

    func average() -> Int {
        return self.count != 0 ? self.total / self.count : 0
    }

    func reset() {
        self.total = 0
        self.count = 0
    }

    func describe() -> Int {
        return self.count
    }
}

struct Splintered {
    var a = 0
    var b = 0

    mutating func setA(_ v: Int) {
        self.a = v
    }

    func getA() -> Int {
        return self.a
    }

    mutating func setB(_ v: Int) {
        self.b = v
    }

    func getB() -> Int {
        return self.b
    }

    func loner() -> Int {
        return 42
    }
}
