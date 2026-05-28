module com.sample.lib {
    requires java.base;

    exports com.sample.api;
    exports com.sample.lib;

    provides com.sample.api.Plugin with com.sample.lib.MyPlugin;
}
