// Fixtures for Apex complexity and nesting walker tests.
public with sharing class Nested {
    public Integer deeplyNested(List<Integer> values) {
        if (values != null) {
            for (Integer v : values) {
                if (v > 0) {
                    while (v > 10) {
                        try {
                            if (v == 50) {
                                return v;
                            }
                        } catch (MathException e) {
                            v = 0;
                        }
                        v--;
                    }
                }
            }
        }
        return 0;
    }

    public String manyBranches(Integer a, Integer b, String s) {
        if (a > 0 && b > 0) {
            return 'both';
        } else if (a > 0 || b > 0) {
            return 'one';
        }
        if (s == null) {
            return 'none';
        }
        String t = s ?? 'x';
        return a > b ? 'a' : 'b';
    }

    public String dispatch(Integer code) {
        switch on code {
            when 1 {
                return 'one';
            }
            when 2, 3 {
                return 'few';
            }
            when else {
                return 'many';
            }
        }
    }

    public Integer shallow(Integer x) {
        return x + 1;
    }

    public Integer total {
        get {
            if (cached == null) {
                cached = 0;
            }
            return cached;
        }
        set;
    }

    public String label { get; set; }

    private Integer cached;
}
