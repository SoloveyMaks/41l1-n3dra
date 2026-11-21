plugins {
    kotlin("jvm") version "2.2.21"
    kotlin("plugin.spring") version "2.2.21"
    id("org.springframework.boot") version "4.0.9"
    id("io.spring.dependency-management") version "1.1.7"
}

group = "com.health.ehr"
version = "1.0.0"

repositories { mavenCentral() }

kotlin {
    jvmToolchain(21)
    compilerOptions.freeCompilerArgs.add("-Xannotation-default-target=param-property")
    providers.gradleProperty("organizerTests").orNull?.let { directory ->
        sourceSets.named("test") { kotlin.srcDir(directory) }
    }
}

val fileUploadVersion = "1.3.2"
val jsoupPropertyVersion = providers.gradleProperty("legacyJsoupVersion").get()

dependencies {
    implementation("org.springframework.boot:spring-boot-starter-webmvc")
    implementation("org.springframework.boot:spring-boot-starter-jdbc")
    implementation("org.springframework.boot:spring-boot-starter-security")
    implementation("org.jetbrains.kotlin:kotlin-reflect")
    runtimeOnly("com.h2database:h2")

    implementation("com.google.code.gson:gson:2.8.5")
    implementation("org.apache.pdfbox:pdfbox:2.0.22")
    implementation("commons-fileupload:commons-fileupload:$fileUploadVersion")
    add(
        "implementation",
        "org.apache.commons:commons-configuration2:2.7",
    )
    implementation("org.jsoup:jsoup:$jsoupPropertyVersion")
    implementation("commons-net:commons-net:3.9.0")

    testImplementation("org.springframework.boot:spring-boot-starter-webmvc-test")
    testImplementation("org.springframework.security:spring-security-test")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

configurations.named("runtimeClasspath") {
    resolutionStrategy.activateDependencyLocking()
}

tasks.test { useJUnitPlatform() }
