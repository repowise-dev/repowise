package calc;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.function.IntUnaryOperator;

public class Calc {
  static final long[] TABLE = build(200_000);

  private final long[] cache = fill(4096);

  public Calc() {}

  public Calc(int seed) {
    cache[0] = seed;
  }

  static long[] build(int n) {
    long[] out = new long[n];
    for (int i = 1; i < n; i++) {
      out[i] = out[i - 1] + (i * 31L) % 7;
    }
    return out;
  }

  static long[] fill(int n) {
    long[] out = new long[n];
    for (int i = 0; i < n; i++) {
      out[i] = (i * 17L) ^ (i >> 3);
    }
    return out;
  }

  public long sum(int n) {
    long s = 0;
    for (int i = 0; i < n; i++) {
      s += cache[i % cache.length] * i;
    }
    return s;
  }

  public long sum(long n) {
    long s = 0;
    for (long i = 0; i < n; i++) {
      s += (i * i) % 13;
    }
    return s;
  }

  public long sum(int n, int step) {
    long s = 0;
    for (int i = 0; i < n; i += step) {
      s += TABLE[i % TABLE.length] * (i & 7) + Long.numberOfTrailingZeros(s | 1);
    }
    return s;
  }

  public long mapAll(int n) {
    IntUnaryOperator f =
        x -> {
          int y = x;
          for (int k = 0; k < 64; k++) {
            y = (y * 31 + k) % 1013;
          }
          return y;
        };
    long s = 0;
    for (int i = 0; i < n; i++) {
      s += f.applyAsInt(i);
    }
    return s;
  }

  public List<String> sorted(int n) {
    List<String> words = new ArrayList<>();
    for (int i = 0; i < n; i++) {
      words.add(Integer.toString((i * 7919) % 10007));
    }
    words.sort(
        new Comparator<String>() {
          @Override
          public int compare(String a, String b) {
            int ha = 0;
            int hb = 0;
            for (int k = 0; k < 16; k++) {
              ha = ha * 31 + a.charAt(k % a.length());
              hb = hb * 31 + b.charAt(k % b.length());
            }
            return Integer.compare(ha, hb);
          }
        });
    return words;
  }

  static final class Box implements Comparable<Box> {
    final int v;

    Box(int v) {
      this.v = v;
    }

    @Override
    public int compareTo(Box o) {
      return Integer.compare(v % 97, o.v % 97);
    }
  }

  static int boxes(int n) {
    List<Box> list = new ArrayList<>();
    for (int i = 0; i < n; i++) {
      list.add(new Box((i * 7919) % 10007));
    }
    list.sort(null);
    return list.get(0).v;
  }

  public static void main(String[] args) {
    long end = System.nanoTime() + Long.parseLong(args[0]) * 1_000_000L;
    long n = 0;
    while (System.nanoTime() < end) {
      Calc c = new Calc((int) n);
      n += c.sum(20_000) + c.sum(20_000L) + c.sum(2_000_000, 3) + c.mapAll(20_000);
      n += c.sorted(2_000).size() + boxes(2_000);
    }
    System.out.println(n);
  }
}
