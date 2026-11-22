from pathlib import Path

from backend.app.artifacts.project_kind import detect_project_kind, unsupported_project_message


def make_tree(root: Path, files: dict[str, str]) -> Path:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def test_kotlin_spring_gradle_is_supported(tmp_path):
    root = make_tree(tmp_path, {
        "build.gradle.kts": "plugins { kotlin(\"jvm\") version \"2.0.0\"; id(\"org.springframework.boot\") version \"3.3.0\" }",
        "settings.gradle.kts": "rootProject.name = \"demo\"",
        "src/main/kotlin/App.kt": "@SpringBootApplication class App",
    })
    kind = detect_project_kind(root)
    assert kind["supported"] is True
    assert kind["language"] == "kotlin"
    assert kind["framework"] == "spring-boot"
    assert kind["kotlinFiles"] == 1
    assert kind["gradleFiles"] == 2


def test_java_only_is_unsupported(tmp_path):
    root = make_tree(tmp_path, {
        "build.gradle.kts": "plugins { java }",
        "src/main/java/App.java": "class App {}",
    })
    kind = detect_project_kind(root)
    assert kind["supported"] is False
    assert kind["language"] == "java"
    assert "Kotlin-файлов не найдено" in unsupported_project_message(kind)


def test_empty_or_random_archive_is_unsupported(tmp_path):
    root = make_tree(tmp_path, {"README.md": "hello", "image.bin": "x"})
    kind = detect_project_kind(root)
    assert kind["supported"] is False
    assert kind["language"] == "other"


def test_kotlin_without_build_file_is_still_supported(tmp_path):
    root = make_tree(tmp_path, {"src/Main.kt": "fun main() {}"})
    kind = detect_project_kind(root)
    assert kind["supported"] is True
    assert "Gradle/Maven сборка не найдена" in " ".join(kind["reasons"])


def test_nested_kotlin_module_is_found(tmp_path):
    root = make_tree(tmp_path, {
        "settings.gradle.kts": "include(\":app\")",
        "app/build.gradle.kts": "plugins { kotlin(\"jvm\") }",
        "app/src/main/kotlin/demo/App.kt": "class App",
    })
    kind = detect_project_kind(root)
    assert kind["supported"] is True
    assert kind["kotlinFiles"] == 1
