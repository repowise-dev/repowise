plugins {
    id("org.springframework.boot") version "3.2.0"
    id("io.spring.dependency-management") version "1.1.4"
    java
    kotlin("jvm") version "1.9.20"
}

dependencies {
    implementation(libs.bundles.spring.web)
    compileOnly(libs.lombok)
    annotationProcessor(libs.lombok)
    implementation(project(":lib"))
}
