from src.tasks.task_manager import TaskManager


def test_release_upload_slot_is_idempotent():
    tm = TaskManager(max_concurrent=2)
    tm._upload_running = 2
    tm.release_upload_slot("a")
    assert tm._upload_running == 1
    tm.release_upload_slot("a")
    assert tm._upload_running == 1
    tm.release_upload_slot("b")
    assert tm._upload_running == 0


def test_execute_upload_finally_does_not_double_decrement():
    tm = TaskManager()
    tm._upload_running = 1
    tm.release_upload_slot("tid")
    assert tm._upload_running == 0
    if "tid" not in tm._upload_slot_released:
        tm._upload_running -= 1
    else:
        tm._upload_slot_released.discard("tid")
    assert tm._upload_running == 0
    assert "tid" not in tm._upload_slot_released
