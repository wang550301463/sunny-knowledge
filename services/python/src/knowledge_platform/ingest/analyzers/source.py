"""Java, Go and TypeScript facts extracted from real Tree-sitter syntax nodes."""
from __future__ import annotations

from pathlib import PurePosixPath

from tree_sitter import Language, Node, Parser
import tree_sitter_go
import tree_sitter_java
import tree_sitter_typescript

from .models import AnalysisContext, AnalyzerOutput, Diagnostic, ImportFact, SymbolFact, stable_id


class TreeSitterAnalyzer:
    name = "tree-sitter"
    extensions = {".java": "java", ".go": "go", ".ts": "typescript", ".tsx": "tsx", ".mts": "typescript", ".cts": "typescript"}

    def supports(self, path: str) -> bool:
        return PurePosixPath(path).suffix in self.extensions

    def analyze(self, context: AnalysisContext) -> AnalyzerOutput:
        dialect = self.extensions[PurePosixPath(context.path).suffix]
        language = "typescript" if dialect == "tsx" else dialect
        grammars = {"java": tree_sitter_java.language, "go": tree_sitter_go.language, "typescript": tree_sitter_typescript.language_typescript, "tsx": tree_sitter_typescript.language_tsx}
        root = Parser(Language(grammars[dialect]())).parse(context.data).root_node
        symbols, imports, diagnostics = [], [], []
        package = None

        def text(node: Node | None) -> str:
            return context.data[node.start_byte:node.end_byte].decode() if node else ""

        def location(node: Node):
            return context.location(node.start_byte, node.end_byte)

        for node in root.named_children:
            if node.type in ("package_declaration", "package_clause") and not node.has_error:
                names = [c for c in node.named_children if c.type in ("identifier", "scoped_identifier", "package_identifier")]
                package = text(names[-1]) if names else None

        java_kinds = {"class_declaration": "class", "interface_declaration": "interface", "enum_declaration": "enum", "record_declaration": "record", "annotation_type_declaration": "annotation", "method_declaration": "method", "constructor_declaration": "constructor"}
        ts_kinds = {"class_declaration": "class", "abstract_class_declaration": "class", "interface_declaration": "interface", "type_alias_declaration": "type_alias", "enum_declaration": "enum", "function_declaration": "function", "generator_function_declaration": "function", "function_signature": "function", "method_definition": "method", "method_signature": "method", "abstract_method_signature": "method", "internal_module": "namespace"}
        go_kinds = {"type_spec": "type", "type_alias": "type_alias", "function_declaration": "function", "method_declaration": "method"}
        kinds = {"java": java_kinds, "typescript": ts_kinds, "go": go_kinds}[language]
        containers = {"class", "interface", "enum", "record", "annotation", "namespace", "function", "method", "constructor"}
        seen_symbols: dict[str, int] = {}

        def visit(node: Node, scope: tuple[str, ...] = ()):
            if node.is_error or node.is_missing:
                diagnostics.append(Diagnostic("syntax_error", f"Tree-sitter reported {node.type}", location(node), "error"))
                return
            name_node = node.child_by_field_name("name")
            kind = kinds.get(node.type)
            if language == "typescript" and node.type == "variable_declarator":
                value = node.child_by_field_name("value")
                if value and value.type in ("arrow_function", "function_expression", "generator_function"):
                    kind = "function"
            name = text(name_node)
            qualified_scope = scope
            if language == "go" and kind == "method":
                receiver = node.child_by_field_name("receiver")
                if receiver:
                    type_names = []
                    stack = [receiver]
                    while stack:
                        child = stack.pop()
                        if child.type == "type_identifier":
                            type_names.append(text(child))
                        stack.extend(reversed(child.named_children))
                    if type_names:
                        qualified_scope += (type_names[0],)
            if kind and name and not node.has_error and name_node.type not in ("computed_property_name", "object_pattern", "array_pattern"):
                value = node.child_by_field_name("value")
                body = node.child_by_field_name("body") or (value.child_by_field_name("body") if value else None)
                signature = context.data[node.start_byte:(body.start_byte if body else node.end_byte)].decode().strip()
                qualified = ".".join(filter(None, (package, *qualified_scope, name)))
                # Normalize layout only, retaining overload types and names.
                identity = stable_id(context.repo_id, "symbol", context.path, kind, qualified, " ".join(signature.split()))
                duplicate = seen_symbols.get(identity, 0)
                seen_symbols[identity] = duplicate + 1
                if duplicate:
                    identity = stable_id(context.repo_id, "symbol", identity, str(duplicate))
                symbols.append(SymbolFact(identity, name, qualified, kind, signature, location(node)))
            if language == "java" and node.type == "import_declaration" and not node.has_error:
                identifier = next((c for c in node.named_children if c.type in ("identifier", "scoped_identifier")), None)
                module = text(identifier)
                if any(c.type == "asterisk" for c in node.named_children):
                    module += ".*"
                imports.append(ImportFact(module, location(node), is_static=any(c.type == "static" for c in node.children)))
            elif language == "go" and node.type == "import_spec" and not node.has_error:
                path_node = node.child_by_field_name("path")
                imports.append(ImportFact(text(path_node)[1:-1], location(node), text(node.child_by_field_name("name")) or None))
            elif language == "typescript" and node.type in ("import_statement", "export_statement") and not node.has_error:
                source = node.child_by_field_name("source")
                if source:
                    imports.append(ImportFact(text(source)[1:-1], location(node), kind="reexport" if node.type == "export_statement" else "import", is_type_only=any(c.type == "type" for c in node.children)))
            child_scope = scope + (name,) if kind in containers and name else scope
            for child in node.named_children:
                visit(child, child_scope)

        visit(root)
        if root.has_error and not diagnostics:
            diagnostics.append(Diagnostic("syntax_error", "Tree-sitter reported malformed source", location(root), "error"))
        module = context.module(package or context.directory, language, "ast")
        file = context.file(language, module_id=module.id, package=package, symbols=tuple(symbols), imports=tuple(imports))
        return AnalyzerOutput(files=(file,), modules=(module,), diagnostics=tuple(diagnostics))