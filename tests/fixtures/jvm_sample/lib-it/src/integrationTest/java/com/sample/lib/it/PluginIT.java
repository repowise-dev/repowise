package com.sample.lib.it;

import com.sample.lib.MyPlugin;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

public class PluginIT {

    @Test
    public void loadsPlugin() {
        MyPlugin plugin = new MyPlugin();
        assertEquals("hello, my-plugin", plugin.name());
    }
}
