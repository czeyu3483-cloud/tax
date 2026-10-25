"""Empty-by-default SQLite policy repository and clause-level hybrid retrieval.

Explicit backend callers import official source text. There is no crawler or UI
ingestion endpoint. Semantic search requires an injected embedding provider.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

from src.policy.urls import official_public_url

Embedding = Callable[[list[str]], Sequence[Sequence[float]]]
SOURCE_TYPES = {'law', 'regulation', 'rule', 'normative_document', 'guidance', 'interpretation'}
VALIDITY_STATES = {'unverified', 'effective', 'not_yet_effective', 'amended', 'repealed', 'expired'}
DEFAULT_DOMAINS = ('gov.cn', 'npc.gov.cn')
_SCHEMA = """
CREATE TABLE IF NOT EXISTS policy_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT OR IGNORE INTO policy_meta VALUES ('schema_version', '1');
CREATE TABLE IF NOT EXISTS documents (
 id TEXT PRIMARY KEY, family_id TEXT NOT NULL, version TEXT NOT NULL,
 title TEXT NOT NULL, document_number TEXT NOT NULL DEFAULT '', number_key TEXT NOT NULL,
 authority TEXT NOT NULL, source_url TEXT NOT NULL, source_type TEXT NOT NULL,
 domain TEXT NOT NULL DEFAULT '', region TEXT NOT NULL DEFAULT '',
 published_on TEXT, effective_on TEXT, valid_until TEXT,
 validity TEXT NOT NULL DEFAULT 'unverified', verified_at TEXT,
 retrieved_at TEXT NOT NULL, content_hash TEXT NOT NULL,
 UNIQUE(family_id, version)
);
CREATE TABLE IF NOT EXISTS clauses (
 id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
 locator TEXT NOT NULL, heading TEXT NOT NULL, text TEXT NOT NULL, ordinal INTEGER NOT NULL,
 UNIQUE(document_id, locator)
);
CREATE VIRTUAL TABLE IF NOT EXISTS clause_fts USING fts5(
 clause_id UNINDEXED, document_id UNINDEXED, title, document_number, body, tokenize='unicode61'
);
CREATE TABLE IF NOT EXISTS clause_vectors (
 clause_id TEXT PRIMARY KEY REFERENCES clauses(id) ON DELETE CASCADE,
 model TEXT NOT NULL, dimensions INTEGER NOT NULL, vector TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS validity_evidence (
 id INTEGER PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
 source_url TEXT NOT NULL, locator TEXT NOT NULL, excerpt TEXT NOT NULL,
 status TEXT NOT NULL, checked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS version_relations (
 id INTEGER PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
 related_document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
 relation TEXT NOT NULL, related_title TEXT NOT NULL, source_url TEXT NOT NULL, excerpt TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS update_records (
 id INTEGER PRIMARY KEY, document_id TEXT, operation TEXT NOT NULL,
 attempted_at TEXT NOT NULL, succeeded_at TEXT, error TEXT, details TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS policy_number ON documents(number_key);
CREATE INDEX IF NOT EXISTS policy_filters ON documents(domain,region,source_type);
CREATE INDEX IF NOT EXISTS clause_document ON clauses(document_id);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _date(value: object, field: str) -> str | None:
    if value is None or value == '':
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise ValueError(f'{field} must be YYYY-MM-DD') from exc


def _number(value: str) -> str:
    return re.sub(r'[\s\[\]【】〔〕（）()号-]', '', value).casefold()


def _terms(value: str) -> list[str]:
    # unicode61 does not segment Chinese: add characters and overlapping bigrams.
    result = re.findall(r'[a-z0-9]+', value.casefold())
    for run in re.findall(r'[\u3400-\u9fff]+', value):
        result.extend(run)
        result.extend(run[i:i + 2] for i in range(len(run) - 1))
    return list(dict.fromkeys(result))


def _query_terms(value: str) -> list[str]:
    # Prefer multi-character Chinese concepts so a common single character does
    # not make unrelated documents match every query.
    result = re.findall(r'[a-z0-9]+', value.casefold())
    for run in re.findall(r'[\u3400-\u9fff]+', value):
        result.extend([run] if len(run) == 1 else
                      [run[i:i + 2] for i in range(len(run) - 1)])
    return list(dict.fromkeys(result))


def _vector(value: Sequence[float]) -> list[float]:
    vector = [float(x) for x in value]
    if not vector or not all(math.isfinite(x) for x in vector):
        raise ValueError('Embedding must be a nonempty finite vector')
    norm = math.sqrt(sum(x * x for x in vector))
    if not norm:
        raise ValueError('Embedding vector must not be zero')
    return [x / norm for x in vector]


class PolicyStore:
    def __init__(self, db_path: str | Path, *, embedding: Embedding | None = None,
                 embedding_model: str | None = None,
                 official_domains: Sequence[str] = DEFAULT_DOMAINS):
        if embedding is not None and not embedding_model:
            raise ValueError('embedding_model is required with an embedding provider')
        self.path = Path(db_path)
        self.embedding, self.embedding_model = embedding, embedding_model
        self.official_domains = tuple(x.lower().strip('.') for x in official_domains)
        self._lock = threading.RLock()
        self.initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.path), timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys = ON')
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create an empty schema; do not import real or demonstration content."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as connection:
            connection.executescript(_SCHEMA)
            version = connection.execute(
                "SELECT value FROM policy_meta WHERE key='schema_version'").fetchone()[0]
            if version != '1':
                raise ValueError('Unsupported policy schema version')

    def _url(self, value: object) -> str:
        if not isinstance(value, str) or not official_public_url(value, self.official_domains):
            raise ValueError('Policy sources require an allowed public official HTTP(S) URL')
        return value

    def upsert_document(self, document: dict, clauses: list[dict]) -> str:
        """Atomically replace a version and its indexes, preserving inbound lineage.

        Document requires id/title/authority/source_url; clauses require locator/text.
        Non-unverified validity requires matching official validity_evidence entries
        with source_url, locator, excerpt, status and checked_at.
        """
        document = dict(document)
        identity, title, authority = (str(document.get(x) or '').strip()
                                      for x in ('id', 'title', 'authority'))
        if not identity or not title or not authority or not clauses:
            raise ValueError('id, title, authority and at least one clause are required')
        url = self._url(document.get('source_url'))
        source_type = document.get('source_type', 'normative_document')
        validity = document.get('validity', 'unverified')
        if source_type not in SOURCE_TYPES or validity not in VALIDITY_STATES:
            raise ValueError('Invalid policy source type or validity state')
        dates = {field: _date(document.get(field), field)
                 for field in ('published_on', 'effective_on', 'valid_until')}
        if (dates['effective_on'] and dates['valid_until']
                and dates['effective_on'] > dates['valid_until']):
            raise ValueError('valid_until precedes effective_on')
        evidence = []
        for item in document.get('validity_evidence', []):
            entry = dict(item)
            self._url(entry.get('source_url'))
            if (entry.get('status') not in VALIDITY_STATES or not entry.get('excerpt')
                    or not entry.get('locator') or not entry.get('checked_at')):
                raise ValueError('Validity evidence requires status, locator, excerpt, checked_at')
            datetime.fromisoformat(entry['checked_at'])
            evidence.append(entry)
        supporting = [x for x in evidence if x['status'] == validity]
        if validity != 'unverified' and not supporting:
            raise ValueError('A verified validity state requires matching official evidence')
        verified_at = max((x['checked_at'] for x in supporting), default=None)
        relations = []
        for item in document.get('relations', []):
            entry = dict(item)
            self._url(entry.get('source_url'))
            if (entry.get('relation') not in {'amends', 'replaces', 'repeals', 'implements'}
                    or not entry.get('related_title') or not entry.get('excerpt')
                    or entry.get('related_document_id') == identity):
                raise ValueError('Invalid version relationship evidence')
            relations.append(entry)
        prepared, locators = [], set()
        for position, item in enumerate(clauses):
            locator = str(item.get('locator') or '').strip()
            body = str(item.get('text') or '').strip()
            if not locator or not body or locator in locators:
                raise ValueError('Each clause requires unique locator and nonempty text')
            locators.add(locator)
            clause_id = hashlib.sha256(f'{identity}\0{locator}'.encode()).hexdigest()
            prepared.append((clause_id, locator, str(item.get('heading') or ''), body, position))
        vectors = None
        if self.embedding:
            vectors = [_vector(x) for x in self.embedding([x[3] for x in prepared])]
            if len(vectors) != len(prepared) or len({len(x) for x in vectors}) != 1:
                raise ValueError('Embedding batch count or dimensions mismatch')
        number = str(document.get('document_number') or '')
        retrieved_at = str(document.get('retrieved_at') or _now())
        datetime.fromisoformat(retrieved_at)
        content_hash = hashlib.sha256(json.dumps(prepared, ensure_ascii=False).encode()).hexdigest()
        values = (identity, str(document.get('family_id') or identity),
                  str(document.get('version') or '1'), title, number, _number(number), authority,
                  url, source_type, str(document.get('domain') or ''),
                  str(document.get('region') or ''), dates['published_on'], dates['effective_on'],
                  dates['valid_until'], validity, verified_at, retrieved_at, content_hash)
        with self._lock, self._connect() as connection:
            fields = ['family_id', 'version', 'title', 'document_number', 'number_key', 'authority', 'source_url', 'source_type', 'domain', 'region', 'published_on', 'effective_on', 'valid_until', 'validity', 'verified_at', 'retrieved_at', 'content_hash']
            assignments = ','.join(f'{field}=excluded.{field}' for field in fields)
            connection.execute('INSERT INTO documents VALUES (' + ','.join('?' * 18)
                               + ') ON CONFLICT(id) DO UPDATE SET ' + assignments, values)
            connection.execute('DELETE FROM clause_fts WHERE document_id=?', (identity,))
            connection.execute('DELETE FROM clauses WHERE document_id=?', (identity,))
            connection.execute('DELETE FROM validity_evidence WHERE document_id=?', (identity,))
            connection.execute('DELETE FROM version_relations WHERE document_id=?', (identity,))
            for index, (clause_id, locator, heading, body, position) in enumerate(prepared):
                connection.execute('INSERT INTO clauses VALUES (?,?,?,?,?,?)',
                                   (clause_id, identity, locator, heading, body, position))
                connection.execute('INSERT INTO clause_fts VALUES (?,?,?,?,?)',
                                   (clause_id, identity, ' '.join(_terms(title)),
                                    ' '.join(_terms(number)), ' '.join(_terms(heading + ' ' + body))))
                if vectors is not None:
                    vector = vectors[index]
                    connection.execute('INSERT INTO clause_vectors VALUES (?,?,?,?)',
                                       (clause_id, self.embedding_model,
                                        len(vector), json.dumps(vector)))
            for item in evidence:
                connection.execute('INSERT INTO validity_evidence '
                                   '(document_id,source_url,locator,excerpt,status,checked_at) '
                                   'VALUES (?,?,?,?,?,?)', (identity, item['source_url'],
                                    item['locator'], item['excerpt'], item['status'],
                                    item['checked_at']))
            for item in relations:
                connection.execute('INSERT INTO version_relations '
                                   '(document_id,related_document_id,relation,related_title,'
                                   'source_url,excerpt) VALUES (?,?,?,?,?,?)',
                                   (identity, item.get('related_document_id'), item['relation'],
                                    item['related_title'], item['source_url'], item['excerpt']))
            connection.execute('INSERT INTO update_records '
                               '(document_id,operation,attempted_at,succeeded_at,details) '
                               'VALUES (?,?,?,?,?)', (identity, 'upsert', _now(), _now(),
                                                      json.dumps({'clauses': len(prepared)})))
        return identity

    def get_document(self, document_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute('SELECT * FROM documents WHERE id=?',
                                     (document_id,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            for table in ('clauses', 'validity_evidence', 'version_relations'):
                order = 'ordinal' if table == 'clauses' else 'id'
                result[table] = [dict(x) for x in connection.execute(
                    f'SELECT * FROM {table} WHERE document_id=? ORDER BY {order}', (document_id,))]
            return result

    def delete_document(self, document_id: str) -> bool:
        with self._lock, self._connect() as connection:
            connection.execute('DELETE FROM clause_fts WHERE document_id=?', (document_id,))
            removed = connection.execute('DELETE FROM documents WHERE id=?',
                                         (document_id,)).rowcount
            if removed:
                connection.execute('INSERT INTO update_records '
                                   '(document_id,operation,attempted_at,succeeded_at) VALUES (?,?,?,?)',
                                   (document_id, 'delete', _now(), _now()))
            return bool(removed)

    def record_update(self, *, operation: str, document_id: str | None = None,
                      succeeded: bool, error: str | None = None, details: dict | None = None) -> None:
        """Record caller-managed outcomes. This method does not fetch anything."""
        with self._lock, self._connect() as connection:
            connection.execute('INSERT INTO update_records '
                               '(document_id,operation,attempted_at,succeeded_at,error,details) '
                               'VALUES (?,?,?,?,?,?)', (document_id, operation, _now(),
                                _now() if succeeded else None, error, json.dumps(details or {})))

    def updates(self, limit: int = 50) -> list[dict]:
        if not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError('limit must be between 1 and 500')
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(
                'SELECT * FROM update_records ORDER BY id DESC LIMIT ?', (limit,))]

    def stats(self) -> dict:
        with self._connect() as connection:
            result = {table: connection.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                      for table in ('documents', 'clauses', 'clause_vectors', 'validity_evidence',
                                    'version_relations', 'update_records')}
        return {**result, 'semantic_available': self.embedding is not None,
                'embedding_model': self.embedding_model if self.embedding else None}

    def search(self, query: str, *, document_number: str | None = None,
               domain: str | None = None, region: str | None = None,
               source_type: str | None = None, validity: str | None = None,
               as_of: str | None = None, limit: int = 10, semantic: bool = True) -> dict:
        """Retrieve clause evidence. A hit never by itself establishes validity.

        Unknown applicability dates stay visible as unknown. Known dates filter
        versions outside as_of. Semantic failure is reported without hiding FTS hits.
        """
        if not isinstance(query, str) or len(query) > 4000:
            raise ValueError('query must be a string of at most 4000 characters')
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError('limit must be between 1 and 100')
        as_of = _date(as_of, 'as_of')
        where, params = [], []
        for field, value in [('number_key', _number(document_number) if document_number else None),
                             ('domain', domain), ('region', region), ('source_type', source_type),
                             ('validity', validity)]:
            if value is not None:
                where.append(f'd.{field}=?')
                params.append(value)
        if as_of:
            where += ['(d.effective_on IS NULL OR d.effective_on<=?)',
                      '(d.valid_until IS NULL OR d.valid_until>=?)']
            params += [as_of, as_of]
        filters = ' WHERE ' + ' AND '.join(where) if where else ''
        terms = _query_terms(query)[:80]
        fts_scores, semantic_scores = {}, {}
        semantic_status = 'disabled' if not semantic else 'unavailable'
        semantic_error = None
        with self._connect() as connection:
            if terms:
                expression = ' OR '.join('"' + x + '"' for x in terms)
                fts_scores = {row['clause_id']: -row['rank'] for row in connection.execute(
                    'SELECT clause_id,bm25(clause_fts,0,0,5,8,1) rank FROM clause_fts '
                    'WHERE clause_fts MATCH ?', (expression,))}
            rows = connection.execute(
                'SELECT c.id clause_id,c.locator,c.heading,c.text,d.* '
                'FROM clauses c JOIN documents d ON d.id=c.document_id' + filters, params).fetchall()
            if semantic and self.embedding and query.strip():
                try:
                    batch = list(self.embedding([query]))
                    if len(batch) != 1:
                        raise ValueError('Embedding query count mismatch')
                    query_vector = _vector(batch[0])
                    for vector_row in connection.execute(
                            'SELECT clause_id,dimensions,vector FROM clause_vectors WHERE model=?',
                            (self.embedding_model,)):
                        if vector_row['dimensions'] == len(query_vector):
                            vector = json.loads(vector_row['vector'])
                            semantic_scores[vector_row['clause_id']] = sum(
                                x * y for x, y in zip(query_vector, vector, strict=True))
                    semantic_status = 'applied' if semantic_scores else 'no_index'
                except Exception as exc:  # noqa: BLE001 - injected provider failures are isolated
                    semantic_status, semantic_error = 'failed', type(exc).__name__
                    semantic_scores = {}
        ranked = []
        query_number = _number(query)
        for row in rows:
            item = dict(row)
            clause_id = item['clause_id']
            exact_number = bool(query_number and item['number_key'] == query_number)
            exact_title = bool(query.strip() and query.strip() == item['title'])
            cosine, keyword = semantic_scores.get(clause_id), fts_scores.get(clause_id, 0)
            if not (keyword or exact_number or exact_title or document_number
                    or (cosine is not None and cosine > 0)):
                continue
            score = (100 if exact_number else 0) + (50 if exact_title else 0)
            score += min(10, keyword) + (max(0, cosine) if cosine is not None else 0)
            item.update(score=score, semantic_score=cosine,
                        match_type='exact_number' if exact_number or document_number else
                        ('exact_title' if exact_title else 'hybrid' if keyword and cosine is not None
                         else 'keyword' if keyword else 'semantic'),
                        applicability='within_recorded_dates' if as_of and item['effective_on']
                        else 'unknown',
                        evidence_note='检索命中不等于已确认政策效力或本案适用性。')
            item.pop('number_key')
            ranked.append(item)
        ranked.sort(key=lambda x: (-x['score'], x['title'], x['locator']))
        selected = ranked[:limit]
        with self._connect() as connection:
            evidence_by_document = {
                identity: [dict(row) for row in connection.execute(
                    'SELECT source_url,locator,excerpt,status,checked_at '
                    'FROM validity_evidence WHERE document_id=? ORDER BY id', (identity,))]
                for identity in {item['id'] for item in selected}
            }
        for item in selected:
            item['validity_evidence'] = evidence_by_document[item['id']]
        return {'results': selected, 'total_matches': len(ranked),
                'retrieval': {'fts': 'applied' if terms else 'skipped',
                              'semantic': semantic_status, 'semantic_error': semantic_error,
                              'embedding_model': self.embedding_model if self.embedding else None},
                'as_of': as_of}
