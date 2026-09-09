"""Durable conflict artifacts contain frozen intent, never a refreshed automatic write."""
import hashlib
import json


def content_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def public_request(request):
    return {key: value for key, value in request.items() if not key.startswith('_')}


def evidence_refs(request):
    content = request.get('content', {})
    refs = list(content.get('evidence', []))
    refs.extend(ref for claim in content.get('claims', []) for ref in claim.get('evidence', []))
    refs.extend(request[key] for key in ('proof', 'before_manifest', 'after_manifest') if isinstance(request.get(key), dict) and 'revision_id' in request[key])
    return list({json.dumps(ref, sort_keys=True): ref for ref in refs}.values())


def conflict_result(task_id, number, code):
    return {'error_code':code, 'conflict_available':True, 'step_number':number,
            'conflict_url':f'/api/v1/tasks/{task_id}/conflicts/{number}'}