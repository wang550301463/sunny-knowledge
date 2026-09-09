"""Compiler-ready static analysis of immutable, caller-provided source snapshots."""
from .models import (
    AnalysisContext, AnalysisResult, Analyzer, AnalyzerOutput, DependencyFact,
    Diagnostic, FileFact, ImportFact, Location, ModuleFact, RelationFact,
    SourceFile, SourceSnapshot, SymbolFact, stable_id,
)
from .registry import AnalyzerRegistry, analyze_snapshot
from .source import TreeSitterAnalyzer

__all__ = [
    "AnalysisContext", "AnalysisResult", "Analyzer", "AnalyzerOutput", "AnalyzerRegistry",
    "DependencyFact", "Diagnostic", "FileFact", "ImportFact", "Location", "ModuleFact",
    "RelationFact", "SourceFile", "SourceSnapshot", "SymbolFact", "TreeSitterAnalyzer",
    "analyze_snapshot", "stable_id",
]