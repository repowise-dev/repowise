package com.sample.lib.internal;

/**
 * Same-package sibling that references Helper with NO import line. The
 * JVM resolves the unqualified identifier `Helper` from the current
 * package; the indexer must honour the same rule (Phase 1 same-package
 * implicit access) so this file is reachable via the package fan-out
 * from MyPlugin's import of com.sample.lib.internal.Helper.
 */
public class HelperUser {

    private final Helper helper = new Helper();

    public String announce() {
        return helper.greet("internal");
    }
}
