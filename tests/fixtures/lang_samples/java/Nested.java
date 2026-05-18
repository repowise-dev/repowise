class Nested {
    int deeplyNested(int x) {
        if (x > 0) {
            for (int i = 0; i < x; i++) {
                if (i % 2 == 0) {
                    while (i > 0) {
                        try {
                            if (i == 5) {
                                return i;
                            }
                        } catch (Exception e) {
                        }
                        i--;
                    }
                }
            }
        }
        return 0;
    }

    int shallow(int x) {
        return x + 1;
    }
}
