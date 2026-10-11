// Fixtures for nesting / cyclomatic-complexity walker tests.

int deeplyNested(int x) {
    if (x > 0) {
        for (int i = 0; i < x; i++) {
            while (i > 0) {
                if (i == 5) {
                    return i;
                }
            }
        }
    }
    return x;
}

int shallow(int x) {
    return x + 1;
}

int manyBranches(int x) {
    if (x == 1) return 1;
    if (x == 2 && x > 0) return 2;
    if (x == 3 || x < 0) return 3;
    if (x == 4) return 4;
    if (x == 5) return 5;
    if (x == 6) return 6;
    return x;
}
