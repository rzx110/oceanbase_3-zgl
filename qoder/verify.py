"""Verification script for Research Desk.

Uses a temporary database for every test. Each test creates its own
ResearchDeskRuntime, runs assertions, and closes the runtime before
the next test starts. No background server is started or stopped.

Scenarios:
1. Old citation returns todo after status change; new citation returns done.
2. After status change, search returns only one (done) entry; limit=1 works.
3. Two logical notes share a search term; one has multiple revisions;
   limit=2 returns both notes' latest states.
4. Idempotent status update: logical note count does not grow; survives restart.
5. Cross-project isolation: same text, different note_id, both traceable.
6. Continue-work items carry usable citations; citation resolves to matching
   source identity and body.
7. Installed SDK Git commit matches the frozen SHA.
"""

import asyncio
import importlib.metadata
import json
import sys
import tempfile
from pathlib import Path

FROZEN_SHA = "9db1753434162c0aa7c9f1189a1722e617880a52"


def _new_runtime(db_path: Path):
    from research_desk.runtime import ResearchDeskRuntime
    return ResearchDeskRuntime(db_path)


async def _search_tuples(rt, project_id, query, limit=20):
    """Return list of (note_id, category, source_id) for quick assertions."""
    results = await rt.search(project_id, query, limit=limit)
    return [(r.note_id, r.category, r.source_id) for r in results]


async def verify_citation_isolation():
    """Scenario 1: old citation returns todo after status change; new returns done."""
    print("=" * 70)
    print("SCENARIO 1: Citation isolation across status change + restart")
    print("=" * 70)

    tmpdir = tempfile.mkdtemp()
    db_path = Path(tmpdir) / "s1.db"

    rt = _new_runtime(db_path)
    await rt.start()
    try:
        await rt.ensure_project("proof", "Proof project")
        await rt.add_note("proof", "one", "revisionprobe title", "revisionprobe body text", category="todo")

        results_before = await rt.search("proof", "revisionprobe")
        assert len(results_before) == 1, f"Expected 1 result before update, got {len(results_before)}"
        assert results_before[0].category == "todo", f"Expected todo, got {results_before[0].category}"
        old_citation = results_before[0].citation

        await rt.update_task_status("proof", "one", "done")

        results_after = await rt.search("proof", "revisionprobe")
        assert len(results_after) == 1, f"Expected 1 result after update, got {len(results_after)}"
        assert results_after[0].category == "done", f"Expected done, got {results_after[0].category}"
        new_citation = results_after[0].citation
        print(f"✓ Search after update: 1 result, category=done")

        old_entry = await rt.get_entry("proof", old_citation)
        assert old_entry["category"] == "todo", f"Old citation should return todo, got {old_entry['category']}"
        assert old_entry["source_id"] == "note:proof:one", f"Old citation source_id mismatch: {old_entry['source_id']}"
        print(f"✓ Old citation returns todo, source_id=note:proof:one")

        new_entry = await rt.get_entry("proof", new_citation)
        assert new_entry["category"] == "done", f"New citation should return done, got {new_entry['category']}"
        assert new_entry["source_id"] == "note:proof:one:r2", f"New citation source_id mismatch: {new_entry['source_id']}"
        print(f"✓ New citation returns done, source_id=note:proof:one:r2")
    finally:
        await rt.stop()

    rt2 = _new_runtime(db_path)
    await rt2.start()
    try:
        await rt2.reload_projects()
        old_after_restart = await rt2.get_entry("proof", old_citation)
        assert old_after_restart["category"] == "todo", f"Old citation should still return todo after restart"
        print(f"✓ Old citation still returns todo after restart")
    finally:
        await rt2.stop()

    print("\n✓ Scenario 1 PASSED\n")


async def verify_search_dedup():
    """Scenario 2: after status change, search returns only one done entry; limit=1 works."""
    print("=" * 70)
    print("SCENARIO 2: Search deduplication — one result per logical note")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "s2.db"
        rt = _new_runtime(db_path)
        await rt.start()
        try:
            await rt.ensure_project("dedup", "Dedup project")
            await rt.add_note("dedup", "one", "revisionprobe", "revisionprobe content", category="todo")
            await rt.update_task_status("dedup", "one", "done")

            tuples = await _search_tuples(rt, "dedup", "revisionprobe", limit=20)
            assert len(tuples) == 1, f"Expected 1 unique note, got {len(tuples)}: {tuples}"
            assert tuples[0] == ("one", "done", "note:dedup:one:r2"), f"Unexpected: {tuples[0]}"
            print(f"✓ Search returns 1 done entry: {tuples[0]}")

            tuples_limited = await _search_tuples(rt, "dedup", "revisionprobe", limit=1)
            assert len(tuples_limited) == 1, f"limit=1 should return 1, got {len(tuples_limited)}"
            assert tuples_limited[0][1] == "done", f"limit=1 should return done, got {tuples_limited[0][1]}"
            print(f"✓ limit=1 returns the done entry")
        finally:
            await rt.stop()

    print("\n✓ Scenario 2 PASSED\n")


async def verify_multi_note_limit():
    """Scenario 3: two logical notes share a term; one has revisions; limit=2 returns both latest."""
    print("=" * 70)
    print("SCENARIO 3: Multiple notes with same term, limit correctness")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "s3.db"
        rt = _new_runtime(db_path)
        await rt.start()
        try:
            await rt.ensure_project("multi", "Multi-note project")
            await rt.add_note("multi", "alpha", "revisionprobe alpha", "alpha body revisionprobe", category="note")
            await rt.add_note("multi", "beta", "revisionprobe beta", "beta body revisionprobe", category="todo")
            await rt.update_task_status("multi", "beta", "done")

            tuples = await _search_tuples(rt, "multi", "revisionprobe", limit=2)
            note_ids = {t[0] for t in tuples}
            assert note_ids == {"alpha", "beta"}, f"Expected both notes, got {note_ids}"
            cats = {t[0]: t[1] for t in tuples}
            assert cats["alpha"] == "note", f"alpha should be note, got {cats['alpha']}"
            assert cats["beta"] == "done", f"beta should be done (latest), got {cats['beta']}"
            print(f"✓ limit=2 returns both notes with latest states: alpha={cats['alpha']}, beta={cats['beta']}")

            src_ids = {t[0]: t[2] for t in tuples}
            assert src_ids["beta"] == "note:multi:beta:r2", f"beta source_id should be r2, got {src_ids['beta']}"
            print(f"✓ beta source_id is latest revision (r2)")
        finally:
            await rt.stop()

    print("\n✓ Scenario 3 PASSED\n")


async def verify_idempotent_count():
    """Scenario 4: repeated same-status update doesn't grow note count; survives restart."""
    print("=" * 70)
    print("SCENARIO 4: Idempotent status update preserves note count")
    print("=" * 70)

    tmpdir = tempfile.mkdtemp()
    db_path = Path(tmpdir) / "s4.db"

    rt = _new_runtime(db_path)
    await rt.start()
    try:
        await rt.ensure_project("idem", "Idempotent project")
        await rt.add_note("idem", "task1", "Task one", "body", category="todo")
        await rt.update_task_status("idem", "task1", "done")

        p = await rt.get_project("idem")
        assert p.note_count == 1, f"Expected note_count=1 after update, got {p.note_count}"
        print(f"✓ note_count=1 after status update")

        result = await rt.update_task_status("idem", "task1", "done")
        assert result["idempotent"], "Same status should be idempotent"
        p2 = await rt.get_project("idem")
        assert p2.note_count == 1, f"note_count should still be 1 after idempotent update, got {p2.note_count}"
        print(f"✓ Idempotent update: note_count stays at 1")
    finally:
        await rt.stop()

    rt2 = _new_runtime(db_path)
    await rt2.start()
    try:
        await rt2.reload_projects()
        p3 = await rt2.get_project("idem")
        assert p3.note_count == 1, f"note_count should be 1 after restart, got {p3.note_count}"
        print(f"✓ After restart: note_count=1")
    finally:
        await rt2.stop()

    print("\n✓ Scenario 4 PASSED\n")


async def verify_cross_project_isolation():
    """Scenario 5: cross-project search isolation; same text, different note_id, both traceable."""
    print("=" * 70)
    print("SCENARIO 5: Cross-project isolation")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "s5.db"
        rt = _new_runtime(db_path)
        await rt.start()
        try:
            await rt.ensure_project("projA", "Project A")
            await rt.ensure_project("projB", "Project B")
            await rt.add_note("projA", "shared", "isolationprobe", "same text in both projects", category="note")
            await rt.add_note("projB", "shared", "isolationprobe", "same text in both projects", category="reference")

            results_a = await rt.search("projA", "isolationprobe")
            results_b = await rt.search("projB", "isolationprobe")
            assert len(results_a) == 1, f"projA should have 1 result, got {len(results_a)}"
            assert len(results_b) == 1, f"projB should have 1 result, got {len(results_b)}"
            assert results_a[0].project_id == "projA", f"projA result should belong to projA"
            assert results_b[0].project_id == "projB", f"projB result should belong to projB"
            print(f"✓ Each project returns 1 result with correct project_id")

            entry_a = await rt.get_entry("projA", results_a[0].citation)
            entry_b = await rt.get_entry("projB", results_b[0].citation)
            assert entry_a["source_id"] == "note:projA:shared", f"projA source_id: {entry_a['source_id']}"
            assert entry_b["source_id"] == "note:projB:shared", f"projB source_id: {entry_b['source_id']}"
            assert entry_a["category"] == "note", f"projA category: {entry_a['category']}"
            assert entry_b["category"] == "reference", f"projB category: {entry_b['category']}"
            print(f"✓ Both notes traceable to distinct source identities and categories")
        finally:
            await rt.stop()

    print("\n✓ Scenario 5 PASSED\n")


async def verify_continue_work_citation():
    """Scenario 6: continue-work items carry usable citations; citation resolves consistently."""
    print("=" * 70)
    print("SCENARIO 6: Continue-work citation consistency")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "s6.db"
        rt = _new_runtime(db_path)
        await rt.start()
        try:
            await rt.ensure_project("cw", "Continue-work project")
            await rt.add_note("cw", "task1", "CW task", "CW task body content", category="todo")

            cw = await rt.continue_work("cw")
            assert cw["total_entries"] == 1, f"Expected 1 entry, got {cw['total_entries']}"

            next_items = cw["sections"].get("next_steps", [])
            assert len(next_items) == 1, f"Expected 1 next_steps item, got {len(next_items)}"
            item = next_items[0]
            assert "citation" in item, "Continue-work item must carry a citation"
            assert item["source_id"] == "note:cw:task1", f"source_id mismatch: {item['source_id']}"
            print(f"✓ Continue-work item has citation and source_id=note:cw:task1")

            citation_data = item["citation"]
            from powercontext.artifacts import MemoryCitation, ArtifactRef
            citation = MemoryCitation(
                memory_ref=ArtifactRef(**citation_data["memory_ref"]),
                entry_id=citation_data["entry_id"],
                entry_version_id=citation_data["entry_version_id"],
            )
            entry = await rt.get_entry("cw", citation)
            assert entry["source_id"] == item["source_id"], \
                f"Citation source_id {entry['source_id']} != item source_id {item['source_id']}"
            assert "CW task body content" in entry.get("body", entry.get("text", "")), \
                "Citation body should match the note body"
            print(f"✓ Citation resolves to matching source identity and body")
        finally:
            await rt.stop()

    print("\n✓ Scenario 6 PASSED\n")


async def verify_sdk_commit():
    """Scenario 7: assert installed SDK Git commit matches frozen SHA."""
    print("=" * 70)
    print("SCENARIO 7: SDK Git commit verification")
    print("=" * 70)

    dist = importlib.metadata.distribution("powercontext")
    direct_url_text = dist.read_text("direct_url.json")
    assert direct_url_text is not None, "direct_url.json not found for powercontext"
    direct_url = json.loads(direct_url_text)

    vcs_info = direct_url.get("vcs_info", {})
    commit_id = vcs_info.get("commit_id", "")
    print(f"  Installed commit: {commit_id}")
    print(f"  Frozen commit:    {FROZEN_SHA}")

    assert commit_id == FROZEN_SHA, \
        f"SDK commit mismatch: installed={commit_id}, expected={FROZEN_SHA}"
    print(f"✓ SDK commit matches frozen SHA")

    print("\n✓ Scenario 7 PASSED\n")


async def main():
    print("\n" + "=" * 70)
    print("RESEARCH DESK VERIFICATION SUITE")
    print("=" * 70 + "\n")

    try:
        await verify_citation_isolation()
        await verify_search_dedup()
        await verify_multi_note_limit()
        await verify_idempotent_count()
        await verify_cross_project_isolation()
        await verify_continue_work_citation()
        await verify_sdk_commit()

        print("=" * 70)
        print("ALL VERIFICATIONS PASSED")
        print("=" * 70)
        return 0
    except Exception:
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
