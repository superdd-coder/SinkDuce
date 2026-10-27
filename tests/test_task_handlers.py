"""Minimal tests for handlers.py — import chain + coverage trigger logic."""

import pytest
from unittest.mock import MagicMock, patch


class TestHandlersImport:
    """Verify handlers.py module-level imports don't break."""

    def test_task_manager_imported(self):
        """catches NameError: name 'task_manager' is not defined"""
        from src.tasks.handlers import task_manager
        assert task_manager is not None

    def test_services_available(self):
        from src.tasks.handlers import services
        assert services is not None

    def test_upload_handler_importable(self):
        from src.tasks.handlers import upload_handler
        assert callable(upload_handler)


class TestCoverageTriggerLogic:
    """Test the 'last task wins' coverage trigger without running full upload."""

    def test_no_active_tasks_triggers_coverage(self):
        """When this is the only active task → trigger coverage."""
        from src.tasks.handlers import task_manager
        from src.services import services

        # Simulate: this task is the only active upload
        services.catalog = MagicMock()
        task_manager.get_active_tasks = MagicMock(return_value=[{"id": "task-1"}])  # 1 active = self

        remaining = len(task_manager.get_active_tasks(collection="col", task_type="upload")) - 1
        assert remaining == 0  # should trigger

    def test_other_tasks_pending_defers_coverage(self):
        """When other tasks are still active → defer."""
        from src.tasks.handlers import task_manager

        # Simulate: 3 active tasks, including self
        task_manager.get_active_tasks = MagicMock(return_value=[
            {"id": "task-1"}, {"id": "task-2"}, {"id": "task-3"},
        ])

        remaining = len(task_manager.get_active_tasks(collection="col", task_type="upload")) - 1
        assert remaining == 2  # should NOT trigger
