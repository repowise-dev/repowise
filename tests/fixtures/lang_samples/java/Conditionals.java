public class Conditionals {
    public int threeOps(boolean a, boolean b, boolean c, boolean d) {
        if (a && b && c && d) {
            return 1;
        }
        return 0;
    }

    public int sixOps(boolean a, boolean b, boolean c, boolean d, boolean e, boolean f, boolean g) {
        while (a && b && c && d && e && f && g) {
            return 1;
        }
        return 0;
    }

    public int twoOps(boolean a, boolean b, boolean c) {
        if (a && b || c) {
            return 1;
        }
        return 0;
    }
}
