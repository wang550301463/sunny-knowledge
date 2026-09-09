import json

import pytest

from knowledge_platform.ingest.analyzers import SourceFile, SourceSnapshot, analyze_snapshot


def analyze(*sources):
    return analyze_snapshot(SourceSnapshot("repo", "rev", tuple(SourceFile(*s) for s in sources)))


def by_name(result, name, evidence="manifest"):
    return next(d for d in result.dependencies if d.name == name and d.evidence == evidence)


def test_go_mod_and_sum_preserve_declared_versions_and_checksum_only_evidence():
    result = analyze(("worker/go.mod", '''module example.com/worker

go 1.23
require (
    example.com/storage v1.2.3
    example.com/indirect v2.0.0 // indirect
)
replace example.com/storage => ../storage
'''), ("worker/go.sum", "example.com/storage v1.2.3 h1:abc=\nexample.com/storage v1.2.3/go.mod h1:def=\n"))
    dep = by_name(result, "example.com/storage")
    assert dep.declared_version == "v1.2.3"
    assert dep.resolved_version is None  # go.sum is NOT the selected module graph.
    assert dep.location.start_line == 5
    assert dep.metadata["replacement"] == "../storage"
    assert dep.resolution == "unresolved"
    assert not by_name(result, "example.com/indirect").direct
    sums = [d for d in result.dependencies if d.evidence == "lockfile"]
    assert len(sums) == 2
    assert all(d.resolution == "checksum_only" and d.resolved_version is None for d in sums)
    assert {d.metadata["checksum"] for d in sums} == {"h1:abc=", "h1:def="}
    assert len(result.modules) == 1
    assert result.modules[0].name == "example.com/worker"


def test_npm_package_json_and_lock_v3_versions_locations_and_nested_instances():
    manifest = '''{
  "name": "@example/web",
  "version": "0.1.0",
  "dependencies": {
    "left-pad": "^1.1.0"
  },
  "devDependencies": {"typescript": "~5.7.0"},
  "optionalDependencies": {"fsevents": "^2.3.0"}
}'''
    lock = {"name": "@example/web", "lockfileVersion": 3, "packages": {
        "": {"name": "@example/web", "dependencies": {"left-pad": "^1.1.0"}},
        "node_modules/left-pad": {"version": "1.3.0", "integrity": "sha512-test"},
        "node_modules/x/node_modules/left-pad": {"version": "1.1.0"},
        "node_modules/typescript": {"version": "5.7.3", "dev": True},
    }}
    result = analyze(("web/package.json", manifest), ("web/package-lock.json", json.dumps(lock, indent=2)))
    declared = by_name(result, "left-pad")
    assert declared.declared_version == "^1.1.0"
    assert declared.resolved_version == "1.3.0"
    assert declared.location.start_line == 5
    assert declared.metadata["resolution_path"] == "web/package-lock.json"
    assert by_name(result, "typescript").scope == "devDependencies"
    locked = [d for d in result.dependencies if d.name == "left-pad" and d.evidence == "lockfile"]
    assert {d.resolved_version for d in locked} == {"1.3.0", "1.1.0"}
    assert len({d.id for d in locked}) == 2
    assert all(d.location.path == "web/package-lock.json" for d in locked)
    assert len(result.modules) == 1
    assert result.modules[0].name == "@example/web"


def test_npm_v1_and_workspace_links_are_not_fabricated_as_resolved_versions():
    result = analyze(("package-lock.json", '{"lockfileVersion":1,"dependencies":{"a":{"version":"1.2.0","dependencies":{"b":{"version":"2.0.0"}}}}}'), ("web/package-lock.json", '{"lockfileVersion":3,"packages":{"node_modules/pkg":{"resolved":"../pkg","link":true}}}'))
    assert by_name(result, "a", "lockfile").resolved_version == "1.2.0"
    assert by_name(result, "b", "lockfile").resolved_version == "2.0.0"
    assert by_name(result, "pkg", "lockfile").resolution == "unresolved"


def test_maven_namespaces_local_properties_management_and_profiles_are_explicit():
    pom = '''<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>example</groupId><artifactId>api</artifactId><version>1.0</version>
  <properties><jackson.version>2.17.2</jackson.version></properties>
  <dependencyManagement><dependencies>
    <dependency><groupId>bom</groupId><artifactId>managed</artifactId><version>4</version></dependency>
  </dependencies></dependencyManagement>
  <dependencies>
    <dependency>
      <groupId>com.fasterxml.jackson.core</groupId><artifactId>jackson-databind</artifactId>
      <version>${jackson.version}</version>
    </dependency>
    <dependency><groupId>example</groupId><artifactId>inherited</artifactId></dependency>
  </dependencies>
  <profiles><profile><id>prod</id><dependencies>
    <dependency><groupId>example</groupId><artifactId>profile-only</artifactId><version>1</version></dependency>
  </dependencies></profile></profiles>
</project>'''
    result = analyze(("api/pom.xml", pom))
    dep = by_name(result, "com.fasterxml.jackson.core:jackson-databind")
    assert dep.declared_version == "2.17.2"
    assert dep.resolved_version is None
    assert dep.metadata["declared_expression"] == "${jackson.version}"
    assert (dep.location.start_line, dep.location.end_line) == (9, 12)
    assert by_name(result, "example:inherited").resolution == "unresolved"
    assert by_name(result, "bom:managed").scope == "dependencyManagement"
    assert by_name(result, "bom:managed").direct is False
    assert by_name(result, "example:profile-only").resolution == "unresolved"
    assert result.modules[0].name == "example:api"


def test_gradle_static_multiline_declarations_and_dynamic_unknowns_without_execution():
    source = '''plugins { id("java") }
// implementation("fake:comment:1")
val version = "2.0"
dependencies {
  implementation(
    "org.example:core:1.2.3"
  )
  testImplementation 'org.junit.jupiter:junit-jupiter:5.11.0'
  implementation("org.example:dynamic:$version")
  implementation(libs.bundles.logging)
  if (project.hasProperty("prod")) {
    runtimeOnly("org.example:conditional:1")
  }
}
tasks.register("danger") { exec { commandLine("touch", "/tmp/never-execute-repo") } }
'''
    result = analyze(("service/build.gradle.kts", source))
    dep = by_name(result, "org.example:core")
    assert dep.declared_version == "1.2.3"
    assert (dep.location.start_line, dep.location.end_line) == (5, 7)
    assert by_name(result, "org.junit.jupiter:junit-jupiter").scope == "testImplementation"
    assert by_name(result, "org.example:dynamic").resolution == "unresolved"
    assert by_name(result, "org.example:conditional").resolution == "unresolved"
    assert any(d.resolution == "unresolved" and "libs.bundles.logging" in d.name for d in result.dependencies)
    assert not any("fake:comment" in d.name for d in result.dependencies)
    assert any(d.code == "gradle_static_only" for d in result.diagnostics)


@pytest.mark.parametrize("path,source", [("package.json", "{"), ("package-lock.json", '{"lockfileVersion": 99}'), ("pom.xml", '<project><dependencies>'), ("go.mod", "module x\nrequire garbage"), ("go.sum", "garbage"), ("build.gradle", "dependencies { implementation(\"x:y:1\"")])
def test_malformed_manifests_report_diagnostics(path, source):
    result = analyze((path, source))
    assert any(d.severity == "error" for d in result.diagnostics)


def test_xml_external_entities_and_doctype_are_rejected():
    result = analyze(("pom.xml", '<!DOCTYPE project [<!ENTITY secret SYSTEM "file:///etc/passwd">]><project><artifactId>&secret;</artifactId></project>'))
    assert result.dependencies == ()
    assert any(d.code == "unsafe_xml" for d in result.diagnostics)


def test_monorepo_duplicate_module_names_are_distinct_and_nearest_manifest_owns_files():
    result = analyze(("a/package.json", '{"name":"same"}'), ("a/src/index.ts", "export function run() {}"), ("b/package.json", '{"name":"same"}'), ("b/src/index.ts", "export function run() {}"))
    assert len(result.modules) == 2
    assert len({m.id for m in result.modules}) == 2
    for source in (f for f in result.files if f.path.endswith(".ts")):
        assert source.module_id == next(m.id for m in result.modules if m.path == source.path[0])
    assert all(r.evidence == "inferred" for r in result.relations if r.kind == "uses")