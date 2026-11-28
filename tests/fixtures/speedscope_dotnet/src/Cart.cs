using System.Linq;

namespace Shop.Billing;

public class Cart
{
    public long Total(int count)
    {
        long sum = 0;
        for (var i = 0; i < count; i++)
        {
            sum += new Line(i).Price();
        }
        return sum;
    }

    public long Total(string count)
    {
        return Total(int.Parse(count)) + Count(count);
    }

    public long Sum(int count)
    {
        return Enumerable.Range(0, count).Select(i => Line.Square(i % 1000)).Sum();
    }

    private static long Count(string text)
    {
        long n = 0;
        for (var i = 0; i < 200_000; i++)
        {
            n += text.Length % 7;
        }
        return n;
    }

    public class Line
    {
        private readonly int _value;

        public Line(int value)
        {
            _value = value;
        }

        public long Price()
        {
            long p = 0;
            for (var k = 0; k < 20; k++)
            {
                p += (_value * 31 + k) % 97;
            }
            return p;
        }

        public static long Square(int x)
        {
            long s = 0;
            for (var k = 0; k < 20; k++)
            {
                s += (long)x * x % (k + 1);
            }
            return s;
        }
    }
}
