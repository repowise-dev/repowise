package com.sample.lib;

import com.sample.api.Plugin;
import com.sample.lib.internal.Helper;

public class MyPlugin implements Plugin {

    private final Helper helper = new Helper();

    @Override
    public String name() {
        return helper.greet("my-plugin");
    }
}
