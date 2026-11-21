plugins {
    kotlin("jvm") version "2.2.21"
    kotlin("plugin.spring") version "2.2.21"
    id("org.springframework.boot") version "4.0.9"
    id("io.spring.dependency-management") version "1.1.7"
}

group = "com.fleet.telematics"
version = "1.0.0"

repositories { mavenCentral() }

kotlin {
    jvmToolchain(21)
    compilerOptions.freeCompilerArgs.add("-Xannotation-default-target=param-property")
    providers.gradleProperty("organizerTests").orNull?.let { directory ->
        sourceSets.named("test") { kotlin.srcDir(directory) }
    }
}

val xstreamVersion = "1.4.15"
val orgJsonPropertyVersion = providers.gradleProperty("legacyOrgJsonVersion").get()

dependencies {
    implementation("org.springframework.boot:spring-boot-starter-webmvc")
    implementation("org.springframework.boot:spring-boot-starter-jdbc")
    implementation("org.springframework.boot:spring-boot-starter-security")
    implementation("org.jetbrains.kotlin:kotlin-reflect")
    runtimeOnly("com.h2database:h2")

    implementation("org.apache.commons:commons-compress:1.24.0")
    implementation("com.hazelcast:hazelcast:5.1.2")
    implementation("com.thoughtworks.xstream:xstream:$xstreamVersion")
    add(
        "implementation",
        "org.mybatis:mybatis:3.5.5",
    )
    implementation("org.json:json:$orgJsonPropertyVersion")
    implementation("org.apache.commons:commons-text:1.10.0")

    testImplementation("org.springframework.boot:spring-boot-starter-webmvc-test")
    testImplementation("org.springframework.security:spring-security-test")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

configurations.named("runtimeClasspath") {
    resolutionStrategy.activateDependencyLocking()
}

tasks.test { useJUnitPlatform() }
