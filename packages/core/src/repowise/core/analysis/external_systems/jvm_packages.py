"""Curated Java package prefix -> Maven ``group:artifact`` table.

A JVM import names a *package* (``com.google.common.collect``) while a manifest
declares a *coordinate* (``com.google.guava:guava``). The two share no naming
rule, and a groupId-prefix guess links wrongly (``com.google.common`` is not
under the ``com.google.guava`` group). So the only mapping used is an explicit
table of well-known artifacts; an import matching nothing stays unlinked.

Each value is a tuple of coordinates that legitimately provide the package,
most specific artifact first. The matcher only ever *offers* them as candidates:
a link forms solely when the repository actually declares one, so an entry
cannot create a link to something the manifests never named.

Keys are matched by longest package prefix, on whole dotted segments, so
``org.junit.jupiter`` wins over ``org.junit`` and ``org.junit`` never matches
``org.junitx``.
"""

from __future__ import annotations

_T = tuple[str, ...]

JVM_PACKAGE_COORDINATES: dict[str, _T] = {
    # Google
    "com.google.common": ("com.google.guava:guava",),
    # Sub-packages under ``com.google.common`` that ship in *other* artifacts;
    # without these the guava prefix above would link them wrongly.
    "com.google.common.truth": ("com.google.truth:truth",),
    "com.google.common.truth.extensions.proto": (
        "com.google.truth.extensions:truth-proto-extension",
    ),
    "com.google.common.testing": ("com.google.guava:guava-testlib",),
    "com.google.common.collect.testing": ("com.google.guava:guava-testlib",),
    "com.google.common.escape.testing": ("com.google.guava:guava-testlib",),
    "com.google.common.util.concurrent.testing": ("com.google.guava:guava-testlib",),
    "com.google.gson": ("com.google.code.gson:gson",),
    # Gson's protobuf adapter is a separate artifact (and, in the Gson repo, a
    # module that generates its own ``generated`` classes); an empty tuple fences
    # the prefix off so it is not linked to gson.
    "com.google.gson.protobuf": (),
    "com.google.gson.protobuf2": (),
    "com.google.protobuf": ("com.google.protobuf:protobuf-java",),
    "com.google.protobuf.util": ("com.google.protobuf:protobuf-java-util",),
    "com.google.inject": ("com.google.inject:guice",),
    "com.google.errorprone.annotations": ("com.google.errorprone:error_prone_annotations",),
    "com.google.auto.value": (
        "com.google.auto.value:auto-value-annotations",
        "com.google.auto.value:auto-value",
    ),
    "org.checkerframework": ("org.checkerframework:checker-qual",),
    # Jackson
    "com.fasterxml.jackson.core": ("com.fasterxml.jackson.core:jackson-core",),
    "com.fasterxml.jackson.databind": ("com.fasterxml.jackson.core:jackson-databind",),
    "com.fasterxml.jackson.annotation": ("com.fasterxml.jackson.core:jackson-annotations",),
    "com.fasterxml.jackson.dataformat.yaml": (
        "com.fasterxml.jackson.dataformat:jackson-dataformat-yaml",
    ),
    "com.fasterxml.jackson.dataformat.xml": (
        "com.fasterxml.jackson.dataformat:jackson-dataformat-xml",
    ),
    "com.fasterxml.jackson.datatype.jsr310": (
        "com.fasterxml.jackson.datatype:jackson-datatype-jsr310",
    ),
    "com.fasterxml.jackson.module.kotlin": ("com.fasterxml.jackson.module:jackson-module-kotlin",),
    # Apache Commons
    "org.apache.commons.lang3": ("org.apache.commons:commons-lang3",),
    "org.apache.commons.lang": ("commons-lang:commons-lang",),
    "org.apache.commons.io": ("commons-io:commons-io",),
    "org.apache.commons.codec": ("commons-codec:commons-codec",),
    "org.apache.commons.cli": ("commons-cli:commons-cli",),
    "org.apache.commons.collections4": ("org.apache.commons:commons-collections4",),
    "org.apache.commons.compress": ("org.apache.commons:commons-compress",),
    "org.apache.commons.math3": ("org.apache.commons:commons-math3",),
    "org.apache.commons.text": ("org.apache.commons:commons-text",),
    # Logging
    "org.slf4j": ("org.slf4j:slf4j-api",),
    "org.apache.logging.log4j": ("org.apache.logging.log4j:log4j-api",),
    "org.apache.logging.log4j.core": ("org.apache.logging.log4j:log4j-core",),
    "ch.qos.logback.classic": ("ch.qos.logback:logback-classic",),
    "ch.qos.logback.core": ("ch.qos.logback:logback-core",),
    # Testing
    "org.junit": ("junit:junit",),
    "org.junit.platform": (),
    "org.junit.jupiter": (
        "org.junit.jupiter:junit-jupiter-api",
        "org.junit.jupiter:junit-jupiter",
    ),
    "org.junit.jupiter.params": (
        "org.junit.jupiter:junit-jupiter-params",
        "org.junit.jupiter:junit-jupiter",
    ),
    "org.hamcrest": (
        "org.hamcrest:hamcrest",
        "org.hamcrest:hamcrest-core",
        "org.hamcrest:hamcrest-library",
    ),
    "org.mockito": ("org.mockito:mockito-core",),
    "org.mockito.kotlin": ("org.mockito.kotlin:mockito-kotlin",),
    "org.assertj.core": ("org.assertj:assertj-core",),
    "org.testng": ("org.testng:testng",),
    "io.mockk": ("io.mockk:mockk",),
    "kotlin.test": ("org.jetbrains.kotlin:kotlin-test",),
    # Spring
    "org.springframework.boot": ("org.springframework.boot:spring-boot",),
    "org.springframework.boot.autoconfigure": (
        "org.springframework.boot:spring-boot-autoconfigure",
    ),
    "org.springframework.boot.test": ("org.springframework.boot:spring-boot-test",),
    "org.springframework.beans": ("org.springframework:spring-beans",),
    "org.springframework.context": ("org.springframework:spring-context",),
    "org.springframework.core": ("org.springframework:spring-core",),
    "org.springframework.jdbc": ("org.springframework:spring-jdbc",),
    "org.springframework.test": ("org.springframework:spring-test",),
    "org.springframework.transaction": ("org.springframework:spring-tx",),
    "org.springframework.web": ("org.springframework:spring-web",),
    "org.springframework.web.servlet": ("org.springframework:spring-webmvc",),
    "org.springframework.data.jpa": ("org.springframework.data:spring-data-jpa",),
    # Jakarta / javax
    "jakarta.annotation": ("jakarta.annotation:jakarta.annotation-api",),
    "jakarta.inject": ("jakarta.inject:jakarta.inject-api",),
    "jakarta.persistence": ("jakarta.persistence:jakarta.persistence-api",),
    "jakarta.servlet": ("jakarta.servlet:jakarta.servlet-api",),
    "jakarta.validation": ("jakarta.validation:jakarta.validation-api",),
    "javax.inject": ("javax.inject:javax.inject",),
    # Kotlin
    "kotlinx.coroutines": ("org.jetbrains.kotlinx:kotlinx-coroutines-core",),
    "kotlinx.serialization": ("org.jetbrains.kotlinx:kotlinx-serialization-core",),
    "kotlinx.serialization.json": ("org.jetbrains.kotlinx:kotlinx-serialization-json",),
    "org.jetbrains.annotations": ("org.jetbrains:annotations",),
    # HTTP / reactive / misc
    "okhttp3": ("com.squareup.okhttp3:okhttp",),
    "okio": ("com.squareup.okio:okio",),
    "retrofit2": ("com.squareup.retrofit2:retrofit",),
    "reactor.core": ("io.projectreactor:reactor-core",),
    "org.reactivestreams": ("org.reactivestreams:reactive-streams",),
    "io.reactivex.rxjava3": ("io.reactivex.rxjava3:rxjava",),
    "io.micrometer": ("io.micrometer:micrometer-core",),
    "org.apache.kafka.clients": ("org.apache.kafka:kafka-clients",),
    "picocli": ("info.picocli:picocli",),
    "lombok": ("org.projectlombok:lombok",),
    "net.bytebuddy": ("net.bytebuddy:byte-buddy",),
    "org.yaml.snakeyaml": ("org.yaml:snakeyaml",),
    "org.jsoup": ("org.jsoup:jsoup",),
    "org.joda.time": ("joda-time:joda-time",),
    "org.json": ("org.json:json",),
    "com.zaxxer.hikari": ("com.zaxxer:HikariCP",),
    # Drivers
    "org.h2": ("com.h2database:h2",),
    "org.postgresql": ("org.postgresql:postgresql",),
    "com.mysql.cj": ("com.mysql:mysql-connector-j",),
}


def jvm_coordinate_candidates(external_name: str) -> tuple[str, ...]:
    """Declared ``group:artifact`` names a Java/Kotlin import may come from.

    Longest package prefix wins, on whole dotted segments. Returns ``()`` for
    anything the table does not know, so unknown imports stay unlinked.
    """
    if "." not in external_name and external_name not in JVM_PACKAGE_COORDINATES:
        return ()
    segments = external_name.split(".")
    for end in range(len(segments), 0, -1):
        coordinates = JVM_PACKAGE_COORDINATES.get(".".join(segments[:end]))
        if coordinates is not None:
            return coordinates
    return ()
