# -*- coding: utf-8 -*-
"""Snapshot-Cache und Unterprozess-Vertrag der Projekt-Discovery."""
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from taskplan import discovery, runner
from taskplan.traversal import Level, Project, TraversalConfig


class TestDiscoveryCache(unittest.TestCase):
    def test_second_scan_comes_from_cache(self):
        tmp = Path(tempfile.mkdtemp())
        root = tmp / "root"
        project = root / "project"
        project.mkdir(parents=True)
        (project / "TODO.md").write_text("todo", encoding="utf-8")
        cache = tmp / "projects-cache.json"
        config = TraversalConfig(
            roots=[root],
            levels=[Level("root"), Level("project", is_work_unit=True)],
            max_depth=2,
            markers=("TODO.md",),
        )
        patches = (
            mock.patch.object(discovery, "traversal_config", return_value=config),
            mock.patch.object(discovery, "discovery_mode", return_value="auto"),
            mock.patch.object(discovery, "registry_file", return_value=""),
            mock.patch.object(discovery, "discovery_cache_config", return_value={
                "enabled": True, "path": cache, "ttl_seconds": 900}),
        )
        with patches[0], patches[1], patches[2], patches[3]:
            first, first_cached = discovery.discover_cached()
            (project / "TODO.md").unlink()
            second, second_cached = discovery.discover_cached()
        self.assertFalse(first_cached)
        self.assertTrue(second_cached)
        self.assertEqual([p.path for p in first], [project])
        self.assertEqual([p.path for p in second], [project])

    def test_refreshes_only_one_root_sector_per_call(self):
        tmp = Path(tempfile.mkdtemp())
        roots = []
        projects = []
        for name in ("root-a", "root-b"):
            root = tmp / name
            project = root / "project"
            project.mkdir(parents=True)
            (project / "TODO.md").write_text("todo", encoding="utf-8")
            roots.append(root)
            projects.append(project)
        cache = tmp / "projects-cache.json"
        config = TraversalConfig(
            roots=roots,
            levels=[Level("root"), Level("project", is_work_unit=True)],
            max_depth=2,
            markers=("TODO.md",),
        )
        patches = (
            mock.patch.object(discovery, "traversal_config", return_value=config),
            mock.patch.object(discovery, "discovery_mode", return_value="auto"),
            mock.patch.object(discovery, "registry_file", return_value=""),
            mock.patch.object(discovery, "discovery_cache_config", return_value={
                "enabled": True, "path": cache, "ttl_seconds": 86400}),
        )
        with patches[0], patches[1], patches[2], patches[3]:
            first = discovery.discover_snapshot()
            second = discovery.discover_snapshot()
            third = discovery.discover_snapshot()
        self.assertEqual(len(first.projects), 1)
        self.assertEqual(first.pending_sectors, 1)
        self.assertEqual({p.path for p in second.projects}, set(projects))
        self.assertEqual(second.pending_sectors, 0)
        self.assertEqual(third.source, "fresh_cache")

    def test_legacy_cache_is_kept_until_sector_refresh_replaces_it(self):
        tmp = Path(tempfile.mkdtemp())
        root = tmp / "root"
        project = root / "project"
        project.mkdir(parents=True)
        cache = tmp / "projects-cache.json"
        cache.write_text(json.dumps({
            "version": 1,
            "signature": "old-config-mtime-dependent-signature",
            "created_at": time.time(),
            "projects": [{"path": str(project), "root_id": root.name}],
        }), encoding="utf-8")
        config = TraversalConfig(
            roots=[root],
            levels=[Level("root"), Level("project", is_work_unit=True)],
            max_depth=2,
            markers=("TODO.md",),
        )
        patches = (
            mock.patch.object(discovery, "traversal_config", return_value=config),
            mock.patch.object(discovery, "discovery_mode", return_value="auto"),
            mock.patch.object(discovery, "registry_file", return_value=""),
            mock.patch.object(discovery, "discovery_cache_config", return_value={
                "enabled": True, "path": cache, "ttl_seconds": 86400}),
        )
        with patches[0], patches[1], patches[2], patches[3]:
            result = discovery.discover_snapshot()
        self.assertEqual([p.path for p in result.projects], [project])
        self.assertEqual(json.loads(cache.read_text())["version"], 2)
        self.assertTrue(any("Legacy-Cache" in w for w in result.warnings))

    def test_signature_ignores_unrelated_config_mtime(self):
        config = TraversalConfig(
            roots=[Path("C:/one")],
            levels=[Level("root"), Level("project", is_work_unit=True)],
            max_depth=2,
            markers=("TODO.md",),
        )
        first = discovery._signature(config, "hybrid", "")
        config.roots = [Path("D:/another")]
        second = discovery._signature(config, "hybrid", "")
        self.assertEqual(first, second)

    def test_policy_change_keeps_lkg_until_sector_refresh(self):
        tmp = Path(tempfile.mkdtemp())
        root = tmp / "root"
        project = root / "project"
        project.mkdir(parents=True)
        cache = tmp / "projects-cache.json"
        cache.write_text(json.dumps({
            "version": 2,
            "signature": "previous-policy",
            "updated_at": time.time(),
            "sectors": [{
                "root_path": str(root),
                "root_id": root.name,
                "refreshed_at": time.time(),
                "attempted_at": 0,
                "projects": [{"path": str(project), "root_id": root.name}],
            }],
        }), encoding="utf-8")
        config = TraversalConfig(
            roots=[root],
            levels=[Level("root"), Level("project", is_work_unit=True)],
            max_depth=2,
            markers=("TODO.md",),
        )
        patches = (
            mock.patch.object(discovery, "traversal_config", return_value=config),
            mock.patch.object(discovery, "discovery_mode", return_value="auto"),
            mock.patch.object(discovery, "registry_file", return_value=""),
            mock.patch.object(discovery, "discovery_cache_config", return_value={
                "enabled": True, "path": cache, "ttl_seconds": 86400}),
            mock.patch.object(
                discovery, "find_projects", side_effect=TimeoutError("cloud")
            ),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            with self.assertRaises(TimeoutError):
                discovery.discover_snapshot()
            result = discovery.read_last_known_good()
        self.assertIsNotNone(result)
        self.assertEqual([p.path for p in result.projects], [project])
        self.assertTrue(result.degraded)
        self.assertTrue(any("Policy" in warning for warning in result.warnings))

    def test_failed_sector_is_rotated_behind_other_due_sector(self):
        tmp = Path(tempfile.mkdtemp())
        roots = [tmp / "root-a", tmp / "root-b"]
        for root in roots:
            root.mkdir()
        config = TraversalConfig(
            roots=roots,
            levels=[Level("root"), Level("project", is_work_unit=True)],
            max_depth=2,
            markers=("TODO.md",),
        )
        signature = discovery._signature(config, "auto", "")
        cache = tmp / "projects-cache.json"
        cache.write_text(json.dumps({
            "version": 2,
            "signature": signature,
            "updated_at": time.time() - 100,
            "sectors": [{
                "root_path": str(root),
                "root_id": root.name,
                "refreshed_at": time.time() - 100,
                "attempted_at": 0,
                "projects": [],
            } for root in roots],
        }), encoding="utf-8")
        scan = mock.Mock(side_effect=[TimeoutError("cloud"), []])
        patches = (
            mock.patch.object(discovery, "traversal_config", return_value=config),
            mock.patch.object(discovery, "discovery_mode", return_value="auto"),
            mock.patch.object(discovery, "registry_file", return_value=""),
            mock.patch.object(discovery, "discovery_cache_config", return_value={
                "enabled": True, "path": cache, "ttl_seconds": 1}),
            mock.patch.object(discovery, "find_projects", scan),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            with self.assertRaises(TimeoutError):
                discovery.discover_snapshot()
            discovery.discover_snapshot()
        self.assertEqual(scan.call_args_list[0].kwargs["only_root"], roots[0])
        self.assertEqual(scan.call_args_list[1].kwargs["only_root"], roots[1])

    def test_task_store_is_final_local_inventory_fallback(self):
        root = Path("C:/allowed")
        config = TraversalConfig(
            roots=[root],
            levels=[Level("root"), Level("project", is_work_unit=True)],
            max_depth=2,
            markers=("TODO.md",),
        )
        store = mock.Mock()
        store.list.return_value = [
            {"project_path": "C:/allowed/one", "root_id": "allowed"},
            {"project_path": "C:/allowed/one", "root_id": "allowed"},
            {"project_path": "D:/outside/two", "root_id": "outside"},
        ]
        with mock.patch.object(
                discovery, "read_last_known_good", return_value=None), \
                mock.patch.object(
                    discovery, "traversal_config", return_value=config):
            result = discovery.fallback_after_failure(store)
        self.assertIsNotNone(result)
        self.assertEqual(result.source, "task_store")
        self.assertEqual(
            [discovery._path_key(p.path) for p in result.projects],
            [discovery._path_key("C:/allowed/one")],
        )

    def test_lkg_precedes_task_store_fallback(self):
        lkg = discovery.DiscoveryResult(
            projects=[Project(Path("C:/cached"), "cached")],
            source="stale_cache",
            degraded=True,
        )
        store = mock.Mock()
        with mock.patch.object(
                discovery, "read_last_known_good", return_value=lkg):
            result = discovery.fallback_after_failure(store)
        self.assertIs(result, lkg)
        store.list.assert_not_called()


class TestBoundedSubprocess(unittest.TestCase):
    def test_valid_payload_is_reconstructed(self):
        payload = {"cached": True, "source": "fresh_cache", "projects": [
            {"path": "C:/portable/project", "root_id": "root"}]}
        completed = subprocess.CompletedProcess(
            args=["python"], returncode=0,
            stdout=json.dumps(payload), stderr="")
        with mock.patch.object(runner.subprocess, "run", return_value=completed):
            projects = runner._discover_projects_bounded(5)
        self.assertEqual(projects[0].root_id, "root")
        self.assertEqual(projects[0].path, Path("C:/portable/project"))

    def test_metadata_is_available_without_breaking_list_api(self):
        payload = {
            "cached": True,
            "source": "stale_cache",
            "degraded": True,
            "projects": [{"path": "C:/portable/project", "root_id": "root"}],
        }
        completed = subprocess.CompletedProcess(
            args=["python"], returncode=0,
            stdout=json.dumps(payload), stderr="")
        with mock.patch.object(runner.subprocess, "run", return_value=completed):
            projects, metadata = runner._discover_projects_bounded(
                5, with_metadata=True
            )
        self.assertEqual(len(projects), 1)
        self.assertEqual(metadata["source"], "stale_cache")
        self.assertTrue(metadata["degraded"])


if __name__ == "__main__":
    unittest.main()
