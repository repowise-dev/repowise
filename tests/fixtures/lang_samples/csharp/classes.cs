// Fixtures for class-level (LCOM4 / god-class) walker tests.
// Explicit `this.` exercises the member-access node type.

class Cohesive
{
    int total;
    int count;

    public void Add(int n)
    {
        this.total += n;
        this.count += 1;
    }

    public int Average()
    {
        return this.count != 0 ? this.total / this.count : 0;
    }

    public void Reset()
    {
        this.total = 0;
        this.count = 0;
    }

    public int Describe()
    {
        return this.count;
    }
}

class Splintered
{
    int a;
    int b;

    public void SetA(int v)
    {
        this.a = v;
    }

    public int GetA()
    {
        return this.a;
    }

    public void SetB(int v)
    {
        this.b = v;
    }

    public int GetB()
    {
        return this.b;
    }

    public int Loner()
    {
        return 42;
    }
}
