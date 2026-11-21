plugins {
    kotlin("jvm") version "2.2.21"
    kotlin("plugin.spring") version "2.2.21"
    id("org.springframework.boot") version "4.0.9"
    id("io.spring.dependency-management") version "1.1.7"
}

group = "com.payment.gateway"
version = "1.0.0"

repositories { mavenCentral() }

kotlin {
    jvmToolchain(21)
    compilerOptions.freeCompilerArgs.add("-Xannotation-default-target=param-property")
    providers.gradleProperty("organizerTests").orNull?.let { directory ->
        sourceSets.named("test") { kotlin.srcDir(directory) }
    }
}

val commonsIoVersion = "2.6"
val jsonSmartPropertyVersion = providers.gradleProperty("legacyJsonSmartVersion").get()

dependencies {
    implementation("org.springframework.boot:spring-boot-starter-webmvc")
    implementation("org.springframework.boot:spring-boot-starter-jdbc")
    implementation("org.springframework.boot:spring-boot-starter-security")
    implementation("org.jetbrains.kotlin:kotlin-reflect")
    runtimeOnly("com.h2database:h2")

    implementation("com.fasterxml.jackson.core:jackson-databind:2.9.8")
    implementation("org.yaml:snakeyaml:1.30")
    implementation("commons-io:commons-io:$commonsIoVersion")
    add(
        "implementation",
        "org.apache.httpcomponents:httpclient:4.5.12",
    )
    implementation("net.minidev:json-smart:$jsonSmartPropertyVersion")
    implementation("org.apache.commons:commons-lang3:3.18.0")

    testImplementation("org.springframework.boot:spring-boot-starter-webmvc-test")
    testImplementation("org.springframework.security:spring-security-test")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

configurations.named("runtimeClasspath") {
    resolutionStrategy.activateDependencyLocking()
}

tasks.test { useJUnitPlatform() }
