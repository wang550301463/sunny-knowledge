"""Deterministic facts only; uploaded narratives remain proposals."""
from __future__ import annotations

import hashlib
import json
from typing import Protocol

from .analyzers import SourceSnapshot, analyze_snapshot
from .analyzers.models import AnalysisResult

COMPILER_VERSION = 'static-wiki-v1'
SCHEMA_VERSION = 'v2'


class LanguageAnalyzer(Protocol):
    def analyze(self, snapshot: SourceSnapshot) -> AnalysisResult: ...


class StaticLanguageAnalyzer:
    def analyze(self, snapshot):
        return analyze_snapshot(snapshot)


def page_id(source_id: str, path: str) -> str:
    return 'page:source:' + source_id + ':' + hashlib.sha256(path.encode()).hexdigest()[:32]


def evidence(snapshot, start=1, end=None):
    text = snapshot['text']
    lines = max(1, text.count('\n') + (0 if text.endswith('\n') else 1))
    return {k: snapshot[k] for k in ('resource_id', 'source_id', 'source_revision', 'path', 'kind')} | {
        'revision_id': snapshot['id'], 'start_line': start, 'end_line': end or lines}


class KnowledgeCompiler:
    version = COMPILER_VERSION
    schema_version = SCHEMA_VERSION

    def code_pages(self, result: AnalysisResult, snapshots: dict[str, dict]):
        pages = []
        for file in result.files:
            snap = snapshots.get(file.path)
            if not snap or snap['kind'] != 'code':
                continue
            ref = evidence(snap)
            claims = [{'id': file.id, 'text': f'{file.path} is a {file.language} source file.',
                       'kind': 'fact', 'entity_type': 'File', 'evidence': [ref]}]
            for symbol in file.symbols[:900]:
                claims.append({'id': symbol.id, 'text': f'Declares {symbol.kind} {symbol.qualified_name}: {symbol.signature}',
                               'kind': 'fact', 'entity_type': 'File', 'evidence': [evidence(snap, symbol.location.start_line, symbol.location.end_line)]})
            # Render only statically observed source declarations. Inferred membership/relations
            # and natural-language business claims do not become automatic formal facts.
            lines = ['# ' + file.path, '', f'Language: {file.language}', '', '## Declarations', '']
            lines += ['- ' + c['text'] for c in claims[1:]]
            imports = [imp.module for imp in file.imports]
            if imports:
                lines += ['', '## Imports', ''] + ['- `' + x + '`' for x in imports]
            pages.append({'page_id': page_id(result.repo_id, file.path), 'path': file.path,
                          'content': {'title': file.path[:512], 'markdown': '\n'.join(lines),
                                      'entity_type': 'File', 'claims': claims, 'evidence': [ref]}})
        return pages

    def narrative_page(self, source_id: str, snapshot: dict):
        return {'page_id': page_id(source_id, snapshot['path']), 'path': snapshot['path'],
                'content': {'title': snapshot['path'][:512], 'markdown': snapshot['text'],
                            'entity_type': 'Incident' if snapshot['kind'] == 'ticket' else None,
                            'claims': [], 'evidence': [evidence(snapshot)]}}

    def validity_ticket(self, snapshot: dict):
        if snapshot['kind'] != 'ticket':
            return None
        obj = json.loads(snapshot['text'])
        if obj.get('type') != 'knowledge_validity_update':
            return None
        required = {'type', 'page_id', 'base_revision', 'claim_ids', 'state', 'reason'}
        if (set(obj) != required or obj['state'] not in {'stale', 'retracted'}
                or not isinstance(obj['claim_ids'], list) or not obj['claim_ids']
                or any(not isinstance(x, str) for x in obj['claim_ids'])
                or obj['claim_ids'] != sorted(set(obj['claim_ids']))
                or any(not isinstance(obj[k], str) or not obj[k] for k in ('page_id', 'base_revision', 'reason'))):
            return None
        return {'page_id': obj['page_id'], 'body': {k: obj[k] for k in required - {'type', 'page_id'}} | {'proof': evidence(snapshot)}}