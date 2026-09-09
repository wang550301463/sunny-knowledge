"""Static build/lockfile adapters. Repository code is never imported or executed."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from pathlib import PurePosixPath
import re
import shlex
from xml.parsers import expat

from .models import AnalysisContext, AnalyzerOutput, DependencyFact, Diagnostic, stable_id


class ManifestError(ValueError):
    pass


def dependency(context, module, name, ecosystem, location, *, instance="", **kwargs):
    scope = kwargs.get("scope", "runtime")
    return DependencyFact(stable_id(context.repo_id, "dependency", context.path, name, scope, instance), module.id, name, ecosystem, location, **kwargs)


def finish(context, module, dependencies, diagnostics=(), *, evidence="manifest"):
    return AnalyzerOutput(files=(context.file(module.language, module_id=module.id, evidence=evidence),), modules=(module,), dependencies=tuple(dependencies), diagnostics=tuple(diagnostics))


def failed(context, code, message):
    return AnalyzerOutput(diagnostics=(Diagnostic(code, message, context.location(0, len(context.data)), "error"),))


class LocatedJSON:
    """Use the standard JSON decoder; retain exact key/value spans by JSON path."""
    def __init__(self, context: AnalysisContext):
        self.context = context
        self.source = context.text
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ManifestError(f"Duplicate JSON key: {key}")
                result[key] = value
            return result
        self.value = json.loads(self.source, object_pairs_hook=unique, parse_constant=lambda value: (_ for _ in ()).throw(ManifestError(f"Invalid JSON constant: {value}")))
        if not isinstance(self.value, dict):
            raise ManifestError("Expected a JSON object")
        self.spans = {}
        self.decoder = json.JSONDecoder()
        self._scan(self._space(0), ())

    def _space(self, offset):
        while offset < len(self.source) and self.source[offset].isspace():
            offset += 1
        return offset

    def _scan(self, offset, path):
        start = offset
        token = self.source[offset]
        if token == "{":
            offset = self._space(offset + 1)
            while self.source[offset] != "}":
                key_start = offset
                key, offset = self.decoder.raw_decode(self.source, offset)
                offset = self._space(offset)
                offset = self._scan(self._space(offset + 1), path + (key,))
                self.spans[path + (key,)] = (key_start, offset)
                offset = self._space(offset)
                if self.source[offset] == ",":
                    offset = self._space(offset + 1)
            offset += 1
        elif token == "[":
            offset = self._space(offset + 1)
            index = 0
            while self.source[offset] != "]":
                offset = self._scan(offset, path + (index,))
                index += 1
                offset = self._space(offset)
                if self.source[offset] == ",":
                    offset = self._space(offset + 1)
            offset += 1
        else:
            _, offset = self.decoder.raw_decode(self.source, offset)
        self.spans[path] = (start, offset)
        return offset

    def location(self, *path):
        start, end = self.spans[path]
        return self.context.location(len(self.source[:start].encode()), len(self.source[:end].encode()))


class NpmAnalyzer:
    name = "npm-manifests"
    sections = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")

    def supports(self, path):
        return PurePosixPath(path).name in ("package.json", "package-lock.json", "npm-shrinkwrap.json")

    def analyze(self, context):
        try:
            document = LocatedJSON(context)
            data = document.value
            module = context.module(str(data.get("name") or context.directory), "typescript", "manifest", version=data.get("version") if isinstance(data.get("version"), str) else None)
            deps = []
            if PurePosixPath(context.path).name == "package.json":
                for scope in self.sections:
                    entries = data.get(scope, {})
                    if not isinstance(entries, dict):
                        raise ManifestError(f"{scope} must be an object")
                    for name, version in entries.items():
                        if not isinstance(version, str):
                            raise ManifestError(f"Dependency version must be a string: {name}")
                        unresolved = version.startswith(("workspace:", "file:", "link:", "git:", "git+", "http:", "https:"))
                        deps.append(dependency(context, module, name, "npm", document.location(scope, name), declared_version=version, scope=scope, resolution="unresolved" if unresolved else "declared", reason="Requires workspace, file or remote resolution" if unresolved else None))
                return finish(context, module, deps)
            version = data.get("lockfileVersion")
            if version not in (1, 2, 3):
                raise ManifestError(f"Unsupported npm lockfileVersion: {version}")
            module = replace(module, evidence="lockfile")
            if version in (2, 3):
                packages = data.get("packages")
                if not isinstance(packages, dict):
                    raise ManifestError("npm lock v2/v3 requires packages object")
                root = packages.get("", {})
                if not isinstance(root, dict):
                    raise ManifestError("npm root package must be an object")
                module = replace(module, name=str(root.get("name") or module.name))
                for install_path, item in packages.items():
                    if not install_path:
                        continue
                    if not isinstance(item, dict):
                        raise ManifestError("npm package record must be an object")
                    name = item.get("name") or install_path.rsplit("node_modules/", 1)[-1]
                    requested = next((root[s][name] for s in self.sections if isinstance(root.get(s), dict) and name in root[s]), None)
                    deps.append(self._locked(context, module, str(name), item, document.location("packages", install_path), install_path, requested))
            else:
                def visit(entries, json_path=(), install_prefix=""):
                    if not isinstance(entries, dict):
                        raise ManifestError("npm dependencies must be an object")
                    for name, item in entries.items():
                        if not isinstance(item, dict):
                            raise ManifestError("npm dependency record must be an object")
                        path = json_path + ("dependencies", name)
                        install_path = f"{install_prefix}node_modules/{name}"
                        deps.append(self._locked(context, module, name, item, document.location(*path), install_path))
                        visit(item.get("dependencies", {}), path, install_path + "/")
                visit(data.get("dependencies", {}))
            return finish(context, module, deps, evidence="lockfile")
        except (ValueError, TypeError, RecursionError) as error:
            return failed(context, "invalid_manifest", f"Invalid npm manifest: {error}")

    @staticmethod
    def _locked(context, module, name, item, location, install_path, requested=None):
        version = item.get("version") if isinstance(item.get("version"), str) else None
        unresolved = item.get("link") or not version
        metadata = {"installation_path": install_path}
        for key in ("integrity", "resolved"):
            if isinstance(item.get(key), str):
                metadata[key] = item[key]
        if isinstance(requested, str):
            metadata["requested_version"] = requested
        return dependency(context, module, name, "npm", location, instance=install_path, resolved_version=None if unresolved else version, evidence="lockfile", scope="devDependencies" if item.get("dev") else "dependencies", direct=None, resolution="unresolved" if unresolved else "resolved", reason="Workspace link or missing locked version" if unresolved else None, metadata=metadata)


def strip_go_comment(line):
    quote = None
    escaped = False
    for index, character in enumerate(line):
        if escaped:
            escaped = False
        elif quote:
            if character == "\\" and quote != "`":
                escaped = True
            elif character == quote:
                quote = None
        elif character in ('"', '`'):
            quote = character
        elif line[index:index + 2] == "//":
            return line[:index], line[index + 2:].strip()
    return line, ""


class GoModAnalyzer:
    name = "go-modules"

    def supports(self, path):
        return PurePosixPath(path).name in ("go.mod", "go.sum")

    def analyze(self, context):
        is_sum = PurePosixPath(context.path).name == "go.sum"
        module = context.module(context.directory, "go", "lockfile" if is_sum else "manifest")
        deps, diagnostics, replacements = [], [], []
        group = None
        offset = 0
        for original in context.data.splitlines(keepends=True):
            line, comment = strip_go_comment(original.decode())
            location = context.location(offset, offset + len(original.rstrip(b"\r\n")))
            offset += len(original)
            try:
                tokens = shlex.split(line)
                if not tokens:
                    continue
                if is_sum:
                    if len(tokens) != 3 or not tokens[1].startswith("v") or not tokens[2].startswith("h1:"):
                        raise ManifestError("Expected module version h1:checksum in go.sum")
                    name, version, checksum = tokens
                    deps.append(dependency(context, module, name, "go", location, instance=version, declared_version=version.removesuffix("/go.mod"), direct=None, resolution="checksum_only", evidence="lockfile", reason="go.sum proves a checksum, not the selected module graph", metadata={"checksum": checksum, "checksum_kind": "go.mod" if version.endswith("/go.mod") else "module"}))
                    continue
                if tokens == [")"]:
                    if group is None:
                        raise ManifestError("Unmatched closing go.mod group")
                    group = None
                    continue
                directive, values = (group, tokens) if group else (tokens[0], tokens[1:])
                if values == ["("]:
                    if group or directive not in {"require", "replace", "exclude", "retract", "tool"}:
                        raise ManifestError("Invalid go.mod group")
                    group = directive
                    continue
                if directive == "module":
                    if len(values) != 1:
                        raise ManifestError("Expected one module path")
                    module = replace(module, name=values[0], location=location)
                elif directive == "require":
                    if len(values) != 2 or not values[1].startswith("v"):
                        raise ManifestError("Expected required module and version")
                    deps.append(dependency(context, module, values[0], "go", location, declared_version=values[1], direct=comment != "indirect", metadata={"version_semantics": "minimum_required"}))
                elif directive == "replace":
                    if "=>" not in values:
                        raise ManifestError("Expected => in replacement")
                    split = values.index("=>")
                    if split not in (1, 2) or len(values[split + 1:]) not in (1, 2):
                        raise ManifestError("Invalid module replacement")
                    replacements.append((values[:split], values[split + 1:], location))
                elif directive in {"exclude", "retract", "tool"}:
                    diagnostics.append(Diagnostic("go_directive_unresolved", f"{directive} retained as an unresolved build constraint", location))
                elif directive not in {"go", "toolchain", "godebug"}:
                    diagnostics.append(Diagnostic("unsupported_go_directive", f"Unknown go.mod directive: {directive}", location))
            except ValueError as error:
                diagnostics.append(Diagnostic("invalid_manifest", str(error), location, "error"))
        if group:
            diagnostics.append(Diagnostic("invalid_manifest", "Unclosed go.mod group", context.location(0, len(context.data)), "error"))
        for old, new, location in replacements:
            for index, dep in enumerate(deps):
                if dep.name == old[0] and (len(old) == 1 or old[1] == dep.declared_version):
                    deps[index] = replace(dep, resolution="unresolved", reason="replace changes module selection; no module resolver was executed", metadata={**dep.metadata, "replacement": " ".join(new), "replacement_line": str(location.start_line)})
        return finish(context, module, deps, diagnostics, evidence="lockfile" if is_sum else "manifest")


@dataclass
class XMLElement:
    name: str
    start: int
    end: int = 0
    text: str = ""
    children: list[XMLElement] = field(default_factory=list)

    def child(self, name):
        return next((c for c in self.children if c.name == name), None)

    def value(self, name):
        child = self.child(name)
        return child.text.strip() if child else None


def parse_xml(context):
    parser = expat.ParserCreate(namespace_separator="}")
    stack, roots = [], []
    def start(name, attrs):
        node = XMLElement(name.rsplit("}", 1)[-1], parser.CurrentByteIndex)
        if stack:
            stack[-1].children.append(node)
        else:
            roots.append(node)
        stack.append(node)
    def end(name):
        node = stack.pop()
        index = parser.CurrentByteIndex
        node.end = context.data.find(b">", index) + 1 if context.data[index:index + 2] == b"</" else index
    def characters(value):
        if stack:
            stack[-1].text += value
    def reject_doctype(*args):
        raise ManifestError("DOCTYPE and entities are not allowed in POM snapshots")
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = characters
    parser.StartDoctypeDeclHandler = reject_doctype
    parser.ExternalEntityRefHandler = lambda *args: 0
    parser.Parse(context.data, True)
    return roots[0]


class MavenAnalyzer:
    name = "maven-pom"

    def supports(self, path):
        return PurePosixPath(path).name == "pom.xml"

    def analyze(self, context):
        try:
            root = parse_xml(context)
            if root.name != "project":
                raise ManifestError("POM root must be project")
        except ManifestError as error:
            return failed(context, "unsafe_xml" if "DOCTYPE" in str(error) else "invalid_manifest", str(error))
        except (expat.ExpatError, IndexError, RecursionError) as error:
            return failed(context, "invalid_manifest", f"Malformed POM: {error}")
        parent = root.child("parent")
        group = root.value("groupId") or (parent.value("groupId") if parent else None)
        artifact = root.value("artifactId")
        version = root.value("version") or (parent.value("version") if parent else None)
        module = context.module(":".join(filter(None, (group, artifact))) or context.directory, "java", "manifest", version=version)
        properties_node = root.child("properties")
        properties = {c.name: c.text.strip() for c in properties_node.children} if properties_node else {}
        properties.update({f"project.{key}": value for key, value in {"groupId": group, "artifactId": artifact, "version": version}.items() if value})
        def interpolate(value):
            if value is None:
                return None
            for _ in range(10):
                newer = re.sub(r"\$\{([^}]+)\}", lambda match: properties.get(match.group(1), match.group(0)), value)
                if newer == value:
                    break
                value = newer
            return value
        deps, diagnostics = [], []
        def visit(node, ancestors):
            if node.name == "dependency" and ancestors and ancestors[-1] == "dependencies":
                location = context.location(node.start, node.end)
                name = ":".join((interpolate(node.value("groupId")) or "?", interpolate(node.value("artifactId")) or "?"))
                raw_version = node.value("version")
                declared = interpolate(raw_version)
                managed = "dependencyManagement" in ancestors
                profile = "profile" in ancestors
                plugin = "plugin" in ancestors
                unknown = not declared or "${" in declared or "${" in name or "?" in name or profile or plugin
                reason = "Profile/plugin activation is not evaluated" if profile or plugin else "Version requires parent/BOM/external property resolution" if unknown else None
                deps.append(dependency(context, module, name, "maven", location, instance=str(node.start), declared_version=declared, scope="dependencyManagement" if managed else node.value("scope") or "compile", direct=False if managed or plugin else None if profile else True, resolution="unresolved" if unknown else "declared", reason=reason, metadata={"declared_expression": raw_version or "", "optional": node.value("optional") or "false", "profile": str(profile).lower()}))
                if unknown:
                    diagnostics.append(Diagnostic("maven_resolution_unknown", reason, location))
            for child in node.children:
                visit(child, ancestors + (node.name,))
        visit(root, ())
        return finish(context, module, deps, diagnostics)