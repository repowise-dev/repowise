package com.sample.lib.dead;

/**
 * Planted dead code: no importer, no framework annotation, no main, no
 * META-INF entry, no JPMS provides. The analyzer MUST flag this file
 * unreachable and the public method as an unused export.
 */
public class ObviouslyDead {

    public String deadGreeting() {
        return "i am dead";
    }
}
