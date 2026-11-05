"""Strict evidence-backed proposals and explicit, audited human acceptance.

There is no model invocation and no automatic approval. A proposal is a complete
replacement snapshot of one task, including its retained historical events.
"""
from __future__ import annotations

import copy
import hashlib
import re
from datetime import datetime

from .ingest import (ADVISORY_LOCK, ImportValidationError, StorageError,
                     begin_import, canonical_json, finish_import,
                     record_failure, sha256_json)

PROPOSAL_VERSION = "task-proposals/1"
CATEGORIES = {"integration", "incident", "support", "change", "other"}
EVENT_KINDS = {"requested", "observed_open", "assigned", "waiting", "resumed",
               "work_ready", "completed", "reopened", "cancelled"}
TIME_BASES = {"event", "message", "notification", "observed"}
HASH_RE = re.compile(r"[0-9a-f]{64}\Z")


class ProposalValidationError(ImportValidationError):
    pass


class ReviewConflict(ProposalValidationError):
    pass


def _object(value, required, optional, where):
    if not isinstance(value, dict):
        raise ProposalValidationError(f"{where}: object required")
    if required - value.keys():
        raise ProposalValidationError(f"{where}: missing required fields: {','.join(sorted(required - value.keys()))}")
    if value.keys() - required - optional:
        raise ProposalValidationError(f"{where}: unknown fields are not accepted")


def _string(value, where, *, nullable=False, max_length=4096):
    if value is None and nullable:
        return
    if not isinstance(value, str) or not value.strip() or "\x00" in value or len(value) > max_length:
        raise ProposalValidationError(f"{where}: nonempty string required")


def _reference(ref, where, *, top_level=False):
    _object(ref, {"message_key", "revision_hash"}, {"event_id"} if top_level else set(), where)
    _string(ref["message_key"], where + ".message_key", max_length=300)
    if not isinstance(ref["revision_hash"], str) or not HASH_RE.fullmatch(ref["revision_hash"]):
        raise ProposalValidationError(f"{where}.revision_hash: lowercase SHA-256 required")
    if top_level and "event_id" in ref:
        _string(ref["event_id"], where + ".event_id", nullable=True, max_length=300)


def all_references(proposal):
    for ref in proposal["evidence"]:
        yield ref
    for event in proposal["events"]:
        yield from event["evidence"]


def validate_document(document: dict) -> dict:
    _object(document, {"schema_version", "proposals"}, {"demo"}, "document")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ProposalValidationError("schema_version: expected integer 1")
    if "demo" in document and type(document["demo"]) is not bool:
        raise ProposalValidationError("demo: boolean required")
    rows = document["proposals"]
    if not isinstance(rows, list) or len(rows) > 5000:
        raise ProposalValidationError("proposals: expected up to 5000 objects; no truncation")
    seen_ids, seen_versions, event_owners = set(), set(), {}
    for i, proposal in enumerate(rows):
        where = f"proposals[{i}]"
        _object(proposal, {"proposal_id", "task_id", "version", "title", "category", "client", "assignee",
                           "external_refs", "events", "evidence"}, {"context_missing", "note"}, where)
        for field in ("proposal_id", "task_id"):
            _string(proposal[field], where + "." + field, max_length=300)
        _string(proposal["title"], where + ".title")
        if type(proposal["version"]) is not int or not 1 <= proposal["version"] <= 2147483647:
            raise ProposalValidationError(f"{where}.version: positive integer required")
        if proposal["proposal_id"] in seen_ids or (proposal["task_id"], proposal["version"]) in seen_versions:
            raise ProposalValidationError(f"{where}: duplicate proposal ID/task version")
        seen_ids.add(proposal["proposal_id"])
        seen_versions.add((proposal["task_id"], proposal["version"]))
        if not isinstance(proposal["category"], str) or proposal["category"] not in CATEGORIES:
            raise ProposalValidationError(f"{where}.category: unsupported value")
        for field in ("client", "assignee"):
            _string(proposal[field], where + "." + field, nullable=True, max_length=500)
        if not isinstance(proposal["external_refs"], dict) or len(canonical_json(proposal["external_refs"])) > 32768:
            raise ProposalValidationError(f"{where}.external_refs: bounded object required")
        for value in proposal["external_refs"].values():
            if value is not None and not isinstance(value, str) and not (
                isinstance(value, list) and all(isinstance(item, str) for item in value)
            ):
                raise ProposalValidationError(f"{where}.external_refs: identifiers must be strings or arrays of strings")
        if 'tenant_id' in proposal['external_refs']:
            from .tenants import TENANTS
            if not isinstance(proposal['external_refs']['tenant_id'], str) or proposal['external_refs']['tenant_id'] not in TENANTS:
                raise ProposalValidationError(f'{where}.external_refs.tenant_id: registered tenant required')
        if "context_missing" in proposal and type(proposal["context_missing"]) is not bool:
            raise ProposalValidationError(f"{where}.context_missing: boolean required")
        if "note" in proposal:
            _string(proposal["note"], where + ".note", nullable=True, max_length=10000)
        if not isinstance(proposal["evidence"], list) or len(proposal["evidence"]) > 10000:
            raise ProposalValidationError(f"{where}.evidence: bounded array required")
        for j, ref in enumerate(proposal["evidence"]):
            _reference(ref, f"{where}.evidence[{j}]", top_level=True)
        events = proposal["events"]
        if not isinstance(events, list) or not 1 <= len(events) <= 10000:
            raise ProposalValidationError(f"{where}.events: expected 1–10000 events")
        seen_events = set()
        for j, event in enumerate(events):
            ew = f"{where}.events[{j}]"
            _object(event, {"event_id", "kind", "occurred_at", "time_basis", "evidence"}, set(), ew)
            _string(event["event_id"], ew + ".event_id", max_length=300)
            event_id = event["event_id"]
            if event_id in seen_events or event_owners.get(event_id, proposal["task_id"]) != proposal["task_id"]:
                raise ProposalValidationError(f"{ew}.event_id: duplicate/cross-task event")
            seen_events.add(event_id)
            event_owners[event_id] = proposal["task_id"]
            if not isinstance(event["kind"], str) or event["kind"] not in EVENT_KINDS:
                raise ProposalValidationError(f"{ew}.kind: unsupported value")
            if not isinstance(event["time_basis"], str) or event["time_basis"] not in TIME_BASES:
                raise ProposalValidationError(f"{ew}.time_basis: unsupported value")
            try:
                dt = datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00"))
                if dt.tzinfo is None or dt.utcoffset() is None:
                    raise ValueError()
            except (AttributeError, ValueError, TypeError) as error:
                raise ProposalValidationError(f"{ew}.occurred_at: timezone-aware datetime required") from error
            if not isinstance(event["evidence"], list) or len(event["evidence"]) > 10000:
                raise ProposalValidationError(f"{ew}.evidence: bounded array required")
            if event["kind"] in {"requested", "completed"} and not event["evidence"]:
                raise ProposalValidationError(f"{ew}.evidence: request/completion needs explicit evidence")
            for k, ref in enumerate(event["evidence"]):
                _reference(ref, f"{ew}.evidence[{k}]")
        for ref in proposal["evidence"]:
            if ref.get("event_id") is not None and ref["event_id"] not in seen_events:
                raise ProposalValidationError(f"{where}.evidence: unknown event_id")
        if not list(all_references(proposal)):
            raise ProposalValidationError(f"{where}: at least one evidence reference required")
        from .domain import validate_event_sequence
        try:
            validate_event_sequence(events)
        except ValueError as error:
            raise ProposalValidationError(f"{where}.events: invalid task lifecycle") from error
    return copy.deepcopy(document)


def evidence_index(bundle: dict) -> set[tuple[str, str]]:
    if not isinstance(bundle, dict) or bundle.get("kind") not in {"telegram-desktop-normalized", "telegram-account-normalized"}:
        raise ProposalValidationError("messages bundle: expected normalized Telegram bundle")
    if not isinstance(bundle.get("messages"), list):
        raise ProposalValidationError("messages bundle: malformed messages")
    result = set()
    for m in bundle["messages"]:
        if not isinstance(m, dict) or not isinstance(m.get("raw_json"), dict):
            raise ProposalValidationError("messages bundle: missing original raw_json")
        if m.get("revision_hash") != sha256_json(m["raw_json"]):
            raise ProposalValidationError("messages bundle: revision hash does not match original record")
        if m.get("message_key") != f"{m.get('source_id')}:{m.get('external_id')}":
            raise ProposalValidationError("messages bundle: invalid message identity")
        result.add((m["message_key"], m["revision_hash"]))
    return result


def verify_offline_evidence(document: dict, available: set[tuple[str, str]]):
    for proposal in document["proposals"]:
        for ref in all_references(proposal):
            if (ref["message_key"], ref["revision_hash"]) not in available:
                raise ProposalValidationError("evidence_not_found: message key/revision absent from supplied bundle")


def verify_pg_evidence(cursor, proposals):
    refs = sorted({(r["message_key"], r["revision_hash"]) for p in proposals for r in all_references(p)})
    if not refs:
        return
    cursor.execute(
        "WITH provided AS (SELECT * FROM unnest(%s::text[],%s::text[]) AS x(message_key,revision_hash)) "
        "SELECT p.message_key FROM provided p LEFT JOIN raw.message_revisions r "
        "ON r.message_key=p.message_key AND r.revision_hash=p.revision_hash WHERE r.message_key IS NULL LIMIT 1",
        ([r[0] for r in refs], [r[1] for r in refs]),
    )
    if cursor.fetchone():
        raise ProposalValidationError("evidence_not_found: message key/revision absent from database")


def ingest_proposals(conn, document, *, content_hash, source_name, allow_demo=False):
    document = validate_document(document)
    if document.get("demo") and not allow_demo:
        raise ProposalValidationError("demo_fixture: explicit --allow-demo required for database import")
    kind = "task-proposals"
    counts = {"proposals_inserted": 0, "unchanged": 0}
    try:
        with conn.transaction():
            with conn.cursor() as cursor:
                run_id, skipped = begin_import(cursor, kind, content_hash, source_name, PROPOSAL_VERSION)
                if skipped:
                    return {"status": "skipped", "import_id": run_id, "reason": "same_successful_file_hash"}
                verify_pg_evidence(cursor, document["proposals"])
                for p in document["proposals"]:
                    digest = sha256_json(p)
                    cursor.execute("SELECT proposal_id,task_id,version,content_hash FROM work.proposals "
                                   "WHERE proposal_id=%s OR (task_id=%s AND version=%s)",
                                   (p["proposal_id"], p["task_id"], p["version"]))
                    existing = cursor.fetchall()
                    if existing:
                        if len(existing) != 1 or existing[0] != (p["proposal_id"], p["task_id"], p["version"], digest):
                            raise ProposalValidationError("proposal_conflict: use a new proposal ID/version; existing documents are immutable")
                        counts["unchanged"] += 1
                        continue
                    cursor.execute("INSERT INTO work.proposals(proposal_id,task_id,version,content_hash,document,state,created_at) "
                                   "VALUES (%s,%s,%s,%s,%s::jsonb,'proposed',now())",
                                   (p["proposal_id"], p["task_id"], p["version"], digest, canonical_json(p)))
                    counts["proposals_inserted"] += 1
                finish_import(cursor, run_id, counts)
        return {"status": "succeeded", "import_id": run_id, **counts}
    except Exception as error:
        record_failure(conn, kind, content_hash, source_name, PROPOSAL_VERSION,
                       "validation_error" if isinstance(error, ImportValidationError) else "database_error")
        if isinstance(error, ImportValidationError):
            raise
        raise StorageError("proposal_import_failed: transaction rolled back") from error


def _snapshot(cursor, task_id):
    cursor.execute("SELECT task_id,title,category,client,assignee,external_refs,revision FROM work.tasks WHERE task_id=%s FOR UPDATE", (task_id,))
    row = cursor.fetchone()
    if not row:
        return None
    task = dict(zip(("task_id", "title", "category", "client", "assignee", "external_refs", "revision"), row))
    cursor.execute("SELECT event_id,kind,occurred_at,time_basis FROM work.events WHERE task_id=%s ORDER BY occurred_at,event_id", (task_id,))
    task["events"] = [{"event_id": r[0], "kind": r[1], "occurred_at": r[2].isoformat(), "time_basis": r[3]} for r in cursor.fetchall()]
    cursor.execute("SELECT message_key,revision_hash,event_id FROM work.evidence WHERE task_id=%s ORDER BY evidence_id", (task_id,))
    task["evidence"] = [dict(zip(("message_key", "revision_hash", "event_id"), r)) for r in cursor.fetchall()]
    return task


def canonical_task(proposal, revision):
    """Explicit nulls replace old values; proposals do not inherit hidden defaults."""
    task = {k: copy.deepcopy(proposal[k]) for k in ("task_id", "title", "category", "client", "assignee", "external_refs")}
    task["revision"] = revision
    task["events"] = [{k: e[k] for k in ("event_id", "kind", "occurred_at", "time_basis")} for e in proposal["events"]]
    refs = {}
    for ref in proposal["evidence"]:
        value = {"message_key": ref["message_key"], "revision_hash": ref["revision_hash"], "event_id": ref.get("event_id")}
        refs[(value["event_id"], value["message_key"], value["revision_hash"])] = value
    for event in proposal["events"]:
        for ref in event["evidence"]:
            value = {"message_key": ref["message_key"], "revision_hash": ref["revision_hash"], "event_id": event["event_id"]}
            refs[(value["event_id"], value["message_key"], value["revision_hash"])] = value
    task["evidence"] = list(refs.values())
    return task


def require_expected_revision(before, expected_revision, *, decision):
    actual = before["revision"] if before else 0
    if expected_revision is None:
        if before and decision == "accept":
            raise ReviewConflict("expected_revision_required: existing tasks need explicit --expected-revision")
        expected_revision = actual
    if type(expected_revision) is not int or expected_revision < 0 or expected_revision != actual:
        raise ReviewConflict("revision_conflict: inspect the current task before retrying")
    return actual


def review_proposal(conn, proposal_id, *, decision, actor, reason=None, expected_revision=None):
    _string(proposal_id, "proposal_id", max_length=300)
    _string(actor, "actor", max_length=300)
    if reason is not None:
        _string(reason, "reason", max_length=10000)
    if decision not in {"accept", "reject"}:
        raise ProposalValidationError("decision: expected accept/reject")
    try:
        with conn.transaction():
            with conn.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK,))
                cursor.execute("SELECT task_id,document,state FROM work.proposals WHERE proposal_id=%s FOR UPDATE", (proposal_id,))
                row = cursor.fetchone()
                if not row:
                    raise ProposalValidationError("proposal_not_found")
                task_id, proposal, state = row
                if state != "proposed":
                    raise ReviewConflict("proposal_already_reviewed: create a new version for further changes")
                proposal = validate_document({"schema_version": 1, "proposals": [proposal]})["proposals"][0]
                before = _snapshot(cursor, task_id)
                revision = require_expected_revision(before, expected_revision, decision=decision)
                after = before
                if decision == "accept":
                    verify_pg_evidence(cursor, [proposal])
                    event_ids = [e["event_id"] for e in proposal["events"]]
                    cursor.execute("SELECT event_id FROM work.events WHERE event_id=ANY(%s::text[]) AND task_id<>%s LIMIT 1", (event_ids, task_id))
                    if cursor.fetchone():
                        raise ProposalValidationError("event_id_conflict: belongs to another task")
                    after = canonical_task(proposal, revision + 1)
                    cursor.execute("INSERT INTO work.tasks(task_id,title,category,client,assignee,external_refs,revision,accepted_at,updated_at) "
                                   "VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,now(),now()) ON CONFLICT(task_id) DO UPDATE SET "
                                   "title=EXCLUDED.title,category=EXCLUDED.category,client=EXCLUDED.client,assignee=EXCLUDED.assignee,"
                                   "external_refs=EXCLUDED.external_refs,revision=EXCLUDED.revision,updated_at=now()",
                                   tuple(after[k] for k in ("task_id", "title", "category", "client", "assignee")) +
                                   (canonical_json(after["external_refs"]), after["revision"]))
                    cursor.execute("DELETE FROM work.evidence WHERE task_id=%s", (task_id,))
                    cursor.execute("DELETE FROM work.events WHERE task_id=%s", (task_id,))
                    cursor.executemany("INSERT INTO work.events(event_id,task_id,kind,occurred_at,time_basis) VALUES (%s,%s,%s,%s,%s)",
                                       [(e["event_id"], task_id, e["kind"], e["occurred_at"], e["time_basis"]) for e in after["events"]])
                    evidence_rows = []
                    for ref in after["evidence"]:
                        identifier = hashlib.sha256(canonical_json([task_id, ref["event_id"], ref["message_key"], ref["revision_hash"]]).encode()).hexdigest()
                        evidence_rows.append((identifier, task_id, ref["event_id"], ref["message_key"], ref["revision_hash"]))
                    cursor.executemany("INSERT INTO work.evidence(evidence_id,task_id,event_id,message_key,revision_hash) VALUES (%s,%s,%s,%s,%s)", evidence_rows)
                cursor.execute("UPDATE work.proposals SET state=%s WHERE proposal_id=%s", ("accepted" if decision == "accept" else "rejected", proposal_id))
                cursor.execute("INSERT INTO ops.review_log(proposal_id,task_id,actor,decision,reason,reviewed_at,before_snapshot,after_snapshot) "
                               "VALUES (%s,%s,%s,%s,%s,now(),%s::jsonb,%s::jsonb)",
                               (proposal_id, task_id, actor, decision, reason, canonical_json(before), canonical_json(after)))
        return {"status": "accepted" if decision == "accept" else "rejected", "proposal_id": proposal_id,
                "task_id": task_id, "task_revision": after["revision"] if after else None}
    except ImportValidationError:
        raise
    except Exception as error:
        raise StorageError("review_failed: transaction rolled back") from error
