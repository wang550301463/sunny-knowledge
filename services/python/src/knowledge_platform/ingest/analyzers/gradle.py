"""Conservative Gradle literal adapter, deliberately not a Groovy/Kotlin evaluator.

A lexical scanner recognizes direct string-coordinate declarations inside dependency
blocks. Conditional declarations, interpolation, aliases and calls stay unresolved.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from .manifests import ManifestError, dependency, failed, finish
from .models import Diagnostic


@dataclass(frozen=True)
class Token:
    text: str
    kind: str
    start: int
    end: int


def tokenize(source):
    tokens = []
    index = 0
    while index < len(source):
        start = index
        char = source[index]
        if char in " \t\r":
            index += 1
            continue
        if source[index:index + 2] == "//":
            end = source.find("\n", index)
            index = len(source) if end < 0 else end
            continue
        if source[index:index + 2] == "/*":
            end = source.find("*/", index + 2)
            if end < 0:
                raise ManifestError("Unclosed Gradle block comment")
            index = end + 2
            continue
        if char in "\"'":
            delimiter = char * 3 if source[index:index + 3] == char * 3 else char
            index += len(delimiter)
            while index < len(source):
                if source[index] == "\\":
                    index += 2
                elif source[index:index + len(delimiter)] == delimiter:
                    index += len(delimiter)
                    break
                else:
                    index += 1
            else:
                raise ManifestError("Unclosed Gradle string")
            tokens.append(Token(source[start:index], "string", start, index))
        elif char.isalpha() or char in "_$":
            index += 1
            while index < len(source) and (source[index].isalnum() or source[index] in "_$."):
                index += 1
            tokens.append(Token(source[start:index], "word", start, index))
        else:
            index += 1
            tokens.append(Token(char, "symbol", start, index))
    return tokens


class GradleAnalyzer:
    name = "gradle-static"

    def supports(self, path):
        return PurePosixPath(path).name in ("build.gradle", "build.gradle.kts")

    def analyze(self, context):
        try:
            tokens = tokenize(context.text)
            matching = {}
            stack = []
            for i, token in enumerate(tokens):
                if token.text in ("{", "(", "["):
                    stack.append(i)
                elif token.text in ("}", ")", "]"):
                    if not stack or tokens[stack[-1]].text != {"}": "{", ")": "(", "]": "["}[token.text]:
                        raise ManifestError("Unmatched Gradle delimiter")
                    matching[stack.pop()] = i
            if stack:
                raise ManifestError("Unclosed Gradle delimiter")
        except ManifestError as error:
            return failed(context, "invalid_manifest", str(error))
        module = context.module(context.directory, "java", "manifest")
        deps = []
        diagnostics = [Diagnostic("gradle_static_only", "Only direct literal dependency coordinates are analyzed; plugins, catalogs, custom build logic and resolution are not evaluated", context.location(0, len(context.data)))]
        source = context.text
        def location(start, end):
            return context.location(len(source[:start].encode()), len(source[:end].encode()))
        def next_non_newline(index):
            while index < len(tokens) and tokens[index].text == "\n":
                index += 1
            return index
        # Every dependencies block is found lexically; a nested block is conservative.
        for block_index, token in enumerate(tokens):
            if token.text != "dependencies":
                continue
            opening = next_non_newline(block_index + 1)
            if opening >= len(tokens) or tokens[opening].text != "{":
                continue
            closing = matching[opening]
            outer_nested = any(tokens[k].text == "{" and k < block_index < matching[k] for k in matching)
            depth = 0
            index = opening + 1
            while index < closing:
                current = tokens[index]
                if current.text == "{":
                    depth += 1
                    index += 1
                    continue
                if current.text == "}":
                    depth -= 1
                    index += 1
                    continue
                following = next_non_newline(index + 1)
                if current.kind != "word" or current.text in {"if", "for", "while", "when", "else", "constraints", "components", "modules", "attributes"} or following >= closing:
                    index += 1
                    continue
                args = []
                end = following
                if tokens[following].text == "(":
                    end = matching[following]
                    args = [t for t in tokens[following + 1:end] if t.text != "\n"]
                elif tokens[following].kind == "string":
                    args = [tokens[following]]
                    while end + 1 < closing and tokens[end + 1].text not in {"\n", ";", "}"}:
                        end += 1
                        args.append(tokens[end])
                else:
                    index += 1
                    continue
                scope = current.text
                expression = source[tokens[following].start:tokens[end].end]
                literal = args[0].text if len(args) == 1 and args[0].kind == "string" else None
                # Escapes/triple quotes are deliberately not interpreted as coordinates.
                value = literal[1:-1] if literal and literal[:3] not in {'"""', "'''"} and "\\" not in literal else None
                parts = value.split(":") if value else []
                coordinate = len(parts) in (3, 4) and all(parts[:2])
                name = ":".join(parts[:2]) if coordinate else expression
                version = parts[2] if coordinate else None
                dynamic = outer_nested or depth > 0 or not coordinate or "$" in value or not version
                loc = location(current.start, tokens[end].end)
                reason = "Dynamic, conditional or unsupported Gradle expression; no build code was executed" if dynamic else None
                deps.append(dependency(context, module, name, "maven", loc, instance=str(sum(d.name == name and d.scope == scope for d in deps)), declared_version=version, scope=scope, resolution="unresolved" if dynamic else "declared", direct=None if dynamic else True, reason=reason, metadata={"expression": expression, "adapter": "gradle-static"}))
                if dynamic:
                    diagnostics.append(Diagnostic("gradle_expression_unresolved", reason, loc))
                index = end + 1
        return finish(context, module, deps, diagnostics)