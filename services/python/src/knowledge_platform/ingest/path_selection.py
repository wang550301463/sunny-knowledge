"""Bounded POSIX globs; selection never executes Git pathspec expressions."""

import re


def validate_patterns(patterns):
    if not isinstance(patterns, list) or len(patterns) > 100:
        raise ValueError("Path filters require at most 100 patterns")
    for value in patterns:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 2048
            or any(ord(char) < 32 for char in value)
            or any(char in value for char in "\\:[]{}")
            or any(part in ("", ".", "..") for part in value.split("/"))
            or any("**" in part and part != "**" for part in value.split("/"))
        ):
            raise ValueError("Expected a relative POSIX glob using *, ? or ** segments")
    return patterns


def compile_pattern(value):
    parts = value.split("/")
    result = ""
    for index, part in enumerate(parts):
        if part == "**":
            result += ".*" if index == len(parts) - 1 else "(?:[^/]+/)*"
        else:
            result += "".join(
                "[^/]*" if char == "*" else "[^/]" if char == "?" else re.escape(char)
                for char in part
            )
            if index != len(parts) - 1:
                result += "/"
    return re.compile(result)


class PathSelection:
    def __init__(self, config):
        self.includes = [compile_pattern(p) for p in config.get("include_paths", [])]
        self.excludes = [compile_pattern(p) for p in config.get("exclude_paths", [])]

    def accepts(self, path):
        return (not self.includes or any(p.fullmatch(path) for p in self.includes)) and not any(
            p.fullmatch(path) for p in self.excludes
        )
