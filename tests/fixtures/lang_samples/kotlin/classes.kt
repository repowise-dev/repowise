// Fixtures for class-level (LCOM4 / god-class) walker tests.
//
// Kotlin accesses instance members WITHOUT a receiver idiomatically; these
// fixtures use an explicit `this.` so the member-access node type is
// exercised. The implicit-receiver case is the documented "no signal" path.

class Cohesive {
    var total = 0
    var count = 0

    fun add(n: Int) {
        this.total += n
        this.count += 1
    }

    fun average(): Int {
        return if (this.count != 0) this.total / this.count else 0
    }

    fun reset() {
        this.total = 0
        this.count = 0
    }

    fun describe(): Int {
        return this.count
    }
}

class Splintered {
    var a = 0
    var b = 0

    fun setA(v: Int) {
        this.a = v
    }

    fun getA(): Int {
        return this.a
    }

    fun setB(v: Int) {
        this.b = v
    }

    fun getB(): Int {
        return this.b
    }

    fun loner(): Int {
        return 42
    }
}
