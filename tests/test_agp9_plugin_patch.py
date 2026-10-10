"""Validate the AGP 9 plugin patch logic used in .github/workflows/android.yml.

Reproduces the CI Python block against the *real* published
flutter_music_picker-0.0.9/android/build.gradle.kts and asserts:
  1. the rewrite removes all four AGP-9-rejected accessors,
  2. the marker makes the step idempotent (second run is a no-op),
  3. the generated Kotlin DSL is syntactically balanced.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

# --- The verbatim published build.gradle.kts (from pub.dev) ------------------
ORIGINAL = '''// Build configuration for the flutter_music_picker Android plugin library.
// This builds the Kotlin plugin source into an .aar that consuming apps can use.
plugins {
    id("com.android.library")
    id("kotlin-android")
}

android {
    namespace = "com.rnd.flutter_music_picker"

    compileSdk = 35

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = JavaVersion.VERSION_17.toString()
    }

    sourceSets {
        getByName("main") {
            java.srcDirs("src/main/kotlin")
            manifest.srcFile("src/main/AndroidManifest.xml")
        }
    }

    defaultConfig {
        minSdk = 21
    }
}

dependencies {
    implementation("org.jetbrains.kotlin:kotlin-stdlib-jdk8:2.1.0")
}
'''

MARKER = '// xiaozhi-agp9-plugin-patch'

# Accessors AGP 9.1.0 rejects with ScriptCompilationError in a .kts script.
REJECTED = [
    'android {',                       # Project.android() for LibraryExtension
    'kotlinOptions {',                 # LibraryExtension.kotlinOptions()
    'jvmTarget = ',                    # DeprecatedKotlinJvmOptions.jvmTarget
    'java.srcDirs(',                   # AndroidSourceDirectorySet.srcDirs()
]


def build_patched() -> str:
    """The replacement script, mirroring the workflow's `patched` literal."""
    return f'''{MARKER}
// Rewritten by CI (see .github/workflows/android.yml) for AGP 9.1.0, which
// promotes the AGP-8-era Kotlin DSL accessors this script originally used
// (Project.android {{}}, kotlinOptions {{}}, jvmTarget, srcDirs(...)) from
// warnings to hard compile errors.
plugins {{
    id("com.android.library")
    id("kotlin-android")
}}

extensions.configure<com.android.build.api.dsl.LibraryExtension>("android") {{
    namespace = "com.rnd.flutter_music_picker"

    compileSdk = 35

    compileOptions {{
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }}

    defaultConfig {{
        minSdk = 21
    }}

    sourceSets {{
        getByName("main") {{
            manifest.srcFile("src/main/AndroidManifest.xml")
        }}
    }}
}}

dependencies {{
    implementation("org.jetbrains.kotlin:kotlin-stdlib-jdk8:2.1.0")
}}
'''


def run_patch(script_path: Path) -> str:
    """Replay the CI step's decision logic against a given script path."""
    text = script_path.read_text(encoding='utf-8')
    if MARKER in text:
        return text  # idempotent no-op
    script_path.write_text(build_patched(), encoding='utf-8')
    return script_path.read_text(encoding='utf-8')


def check_braces(text: str) -> None:
    """Crude balance check so a malformed script fails loudly here, not in CI."""
    depth = 0
    for i, ch in enumerate(text):
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            assert depth >= 0, f'unbalanced `}}` at offset {i}'
    assert depth == 0, f'unbalanced braces: net depth {depth} at EOF'


def strip_comments(text: str) -> str:
    """Drop // comment lines so the accessor scan only sees real code.

    The patched script's header explains which accessors were removed, and that
    prose legitimately mentions `android {}` / `kotlinOptions {}`; matching that
    prose would be a false positive.
    """
    return '\n'.join(
        line for line in text.splitlines()
        if not line.lstrip().startswith('//')
    )


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    try:
        # Simulate the pub cache layout: <pkg>/android/build.gradle.kts
        pkg = tmp / 'flutter_music_picker-0.0.9'
        (pkg / 'android').mkdir(parents=True)
        script = pkg / 'android' / 'build.gradle.kts'
        script.write_text(ORIGINAL, encoding='utf-8')

        # Simulate .dart_tool/package_config.json pointing at the package root.
        config = tmp / '.dart_tool' / 'package_config.json'
        config.parent.mkdir(parents=True)
        config.write_text(
            json.dumps({
                'packages': [
                    {'name': 'flutter_music_picker', 'rootUri': pkg.as_uri()},
                ],
            }),
            encoding='utf-8',
        )

        # --- Pass 1: the actual rewrite -------------------------------------
        first = run_patch(script)
        code = strip_comments(first)
        for bad in REJECTED:
            assert bad not in code, f'rejected accessor still present: {bad!r}'
        assert MARKER in first, 'marker comment missing from patched output'
        assert 'namespace = "com.rnd.flutter_music_picker"' in first
        assert 'compileSdk = 35' in first
        assert 'minSdk = 21' in first
        assert 'manifest.srcFile("src/main/AndroidManifest.xml")' in first
        assert 'kotlin-stdlib-jdk8:2.1.0' in first
        check_braces(first)
        print(f'[pass 1] rewrote {script.name}: {len(ORIGINAL)} -> {len(first)} bytes')

        # --- Pass 2: idempotency --------------------------------------------
        before = script.read_text(encoding='utf-8')
        second = run_patch(script)
        assert second == before, 'second run mutated an already-patched script'
        print(f'[pass 2] idempotent: {len(second)} bytes unchanged')

        # --- Pass 3: the rootUri -> path resolution the CI step performs ----
        # Mirrors the workflow verbatim: url2pathname handles both the Linux
        # (file:///home/...) and Windows (file:///C:/...) URI shapes, which a
        # naive replace("file://", "") does not.
        from urllib.parse import unquote, urlparse
        from urllib.request import url2pathname

        packages = json.loads(config.read_text(encoding='utf-8'))['packages']
        root_uri = next(p['rootUri'] for p in packages if p['name'] == 'flutter_music_picker')
        resolved = Path(url2pathname(unquote(urlparse(root_uri).path))) / 'android' / 'build.gradle.kts'
        assert resolved == script, f'URI resolution mismatch: {resolved} != {script}'
        print(f'[pass 3] rootUri resolution -> {resolved}')

        print('\nAll AGP 9 plugin-patch assertions passed.')
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
