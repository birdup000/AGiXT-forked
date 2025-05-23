import asyncio
import pytest
from agixt.active_task_manager import ActiveTaskManager

@pytest.fixture
def manager():
    return ActiveTaskManager()

async def dummy_coro(duration=0.01, raise_cancel=False):
    try:
        await asyncio.sleep(duration)
        return "done"
    except asyncio.CancelledError:
        if raise_cancel:
            raise
        return "cancelled_in_coro"

@pytest.mark.asyncio
async def test_register_unregister_task(manager: ActiveTaskManager):
    conversation_id = "test_conv_123"
    task = asyncio.create_task(dummy_coro())

    # Check initial state (optional, depends on how you want to expose internal state)
    # For now, we'll assume no direct public way to see tasks, test via behavior.
    # Or, we can add a helper like get_task_count() or is_task_registered() to ActiveTaskManager for testing.
    # Let's assume for now we can inspect _tasks for testing purposes.
    assert len(manager._tasks) == 0

    await manager.register_task(conversation_id, task)
    assert conversation_id in manager._tasks
    assert manager._tasks[conversation_id] is task

    await manager.unregister_task(conversation_id)
    assert conversation_id not in manager._tasks
    assert len(manager._tasks) == 0

    # Clean up the task
    await task # Ensure it completes

@pytest.mark.asyncio
async def test_cancel_task_found(manager: ActiveTaskManager):
    conversation_id = "test_cancel_found_456"
    
    # Create a task that handles cancellation gracefully for the test
    long_task = asyncio.create_task(dummy_coro(duration=5, raise_cancel=True))
    
    await manager.register_task(conversation_id, long_task)
    assert conversation_id in manager._tasks

    cancelled_result = await manager.cancel_task(conversation_id)
    assert cancelled_result is True

    with pytest.raises(asyncio.CancelledError):
        await long_task # The task should raise CancelledError

    # Optionally, check if task is still in manager or removed after cancellation attempt
    # Current ActiveTaskManager.cancel_task does not remove it automatically,
    # it relies on unregister_task to be called separately.
    # If it were to be removed, this assertion would change:
    assert conversation_id in manager._tasks # Still there, as per current ATM.cancel_task
    assert long_task.cancelled() # Check if the task itself is marked as cancelled

    # Clean up by unregistering
    await manager.unregister_task(conversation_id)
    assert conversation_id not in manager._tasks


@pytest.mark.asyncio
async def test_cancel_task_not_found(manager: ActiveTaskManager):
    conversation_id = "test_cancel_not_found_789"
    
    cancelled_result = await manager.cancel_task(conversation_id)
    assert cancelled_result is False

@pytest.mark.asyncio
async def test_cancel_task_already_completed(manager: ActiveTaskManager):
    conversation_id = "test_cancel_completed_101"
    short_task = asyncio.create_task(dummy_coro(duration=0.01)) # Task that completes quickly

    await manager.register_task(conversation_id, short_task)
    assert conversation_id in manager._tasks

    await short_task # Wait for the task to complete
    assert short_task.done() is True
    assert short_task.cancelled() is False

    # Attempt to cancel the already completed task
    # The behavior of task.cancel() on an already done task is that it returns False
    # and does not mark it as cancelled.
    # Our ActiveTaskManager.cancel_task calls task.cancel()
    cancelled_result = await manager.cancel_task(conversation_id)
    
    # If the task was found, cancel_task returns True because it called task.cancel().
    # The underlying task.cancel() on a completed task returns False, but our wrapper
    # returns True if the task was found and cancel() was called.
    assert cancelled_result is True 
    
    assert short_task.done() is True # Still done
    assert short_task.cancelled() is False # Still not cancelled

    # Clean up by unregistering
    await manager.unregister_task(conversation_id)
    assert conversation_id not in manager._tasks

@pytest.mark.asyncio
async def test_register_overwrites_existing_task(manager: ActiveTaskManager):
    conversation_id = "test_overwrite_001"
    task1 = asyncio.create_task(dummy_coro(duration=0.1))
    task2 = asyncio.create_task(dummy_coro(duration=0.1))

    await manager.register_task(conversation_id, task1)
    assert manager._tasks[conversation_id] is task1

    await manager.register_task(conversation_id, task2) # This should overwrite task1
    assert manager._tasks[conversation_id] is task2
    assert task1.cancelled() is False # task1 should not be cancelled by overwrite, just replaced

    # Clean up
    await manager.unregister_task(conversation_id)
    await task1 # Ensure task1 completes or is handled if it was cancelled
    await task2 # Ensure task2 completes
    # If task1 was not automatically cancelled, it should complete normally.
    # If the design was to cancel previous task, then we'd check for task1.cancelled() == True.
    # Based on current ActiveTaskManager, it just overwrites the reference.
    # Consider if task1 should be cancelled automatically by register_task.
    # For now, assuming it's not. The test reflects this.
    # If it should be, the test needs `with pytest.raises(asyncio.CancelledError): await task1`

@pytest.mark.asyncio
async def test_unregister_non_existent_task(manager: ActiveTaskManager):
    conversation_id = "test_unregister_non_existent_002"
    # No error should be raised, and it should run fine.
    await manager.unregister_task(conversation_id)
    assert conversation_id not in manager._tasks

# It might be good to also test the lock mechanism if complex interactions are expected,
# but for now, the basic operations cover the core logic.
# For example, concurrent registrations/cancellations for the same ID.
# However, pytest-asyncio handles running tests in separate event loops usually,
# so direct concurrency testing needs more setup (e.g. threads or specific asyncio patterns).

# Example of a task that might be cancelled mid-execution
async def long_running_cancellable_coro():
    for i in range(10): # Simulate work
        await asyncio.sleep(0.1) # Checkpoint for cancellation
    return "completed_long_run"

@pytest.mark.asyncio
async def test_cancel_long_running_task_actually_stops_it(manager: ActiveTaskManager):
    conversation_id = "test_long_cancel_003"
    task = asyncio.create_task(long_running_cancellable_coro())
    
    await manager.register_task(conversation_id, task)
    
    # Give the task a moment to start
    await asyncio.sleep(0.05) 
    
    cancelled = await manager.cancel_task(conversation_id)
    assert cancelled is True
    
    with pytest.raises(asyncio.CancelledError):
        await task
        
    assert task.cancelled() is True
    await manager.unregister_task(conversation_id)

@pytest.mark.asyncio
async def test_task_completes_then_unregister(manager: ActiveTaskManager):
    conversation_id = "test_complete_unregister_004"
    task = asyncio.create_task(dummy_coro(duration=0.05))
    
    await manager.register_task(conversation_id, task)
    
    result = await task # Wait for completion
    assert result == "done"
    
    await manager.unregister_task(conversation_id)
    assert conversation_id not in manager._tasks
    assert task.done() is True
    assert task.cancelled() is False

@pytest.mark.asyncio
async def test_cancel_task_then_unregister(manager: ActiveTaskManager):
    conversation_id = "test_cancel_unregister_005"
    task = asyncio.create_task(long_running_cancellable_coro())
    
    await manager.register_task(conversation_id, task)
    await asyncio.sleep(0.01) # let it start
    
    cancelled_result = await manager.cancel_task(conversation_id)
    assert cancelled_result is True
    
    with pytest.raises(asyncio.CancelledError):
        await task
        
    assert task.cancelled() is True
    
    await manager.unregister_task(conversation_id)
    assert conversation_id not in manager._tasks

# Test for ActiveTaskManager's behavior when a task finishes on its own
# and is then attempted to be cancelled.
@pytest.mark.asyncio
async def test_cancel_task_that_finished_itself(manager: ActiveTaskManager):
    conversation_id = "test_finished_self_cancel_006"
    task = asyncio.create_task(dummy_coro(duration=0.02)) # Finishes quickly
    await manager.register_task(conversation_id, task)

    await asyncio.sleep(0.1) # Ensure task is well and truly finished

    assert task.done() is True
    assert task.cancelled() is False

    # Attempt to cancel it via the manager
    # cancel_task() in ActiveTaskManager currently returns True if the task was found
    # and task.cancel() was called, regardless of whether task.cancel() itself returned True or False.
    cancel_attempt_result = await manager.cancel_task(conversation_id)
    assert cancel_attempt_result is True 

    # The task itself should not be marked as cancelled because it was already done.
    assert task.cancelled() is False
    assert task.done() is True # Still done

    await manager.unregister_task(conversation_id)
    assert conversation_id not in manager._tasks

    # Ensure no background errors if the task was already awaited/handled
    # (pytest-asyncio usually handles this well)
    final_result = await task # Should return "done"
    assert final_result == "done"

# Test the internal state directly if needed, though behavior testing is preferred.
# This test assumes that `_tasks` is an accessible attribute for inspection.
@pytest.mark.asyncio
async def test_internal_task_storage(manager: ActiveTaskManager):
    task1_id = "conv_task1"
    task2_id = "conv_task2"
    
    dummy_task1 = asyncio.create_task(dummy_coro(0.01))
    dummy_task2 = asyncio.create_task(dummy_coro(0.01))

    assert len(manager._tasks) == 0, "Manager should be empty initially."

    await manager.register_task(task1_id, dummy_task1)
    assert len(manager._tasks) == 1, "Manager should have 1 task after first registration."
    assert manager._tasks.get(task1_id) is dummy_task1, "Task1 not stored correctly."

    await manager.register_task(task2_id, dummy_task2)
    assert len(manager._tasks) == 2, "Manager should have 2 tasks after second registration."
    assert manager._tasks.get(task2_id) is dummy_task2, "Task2 not stored correctly."

    await manager.unregister_task(task1_id)
    assert len(manager._tasks) == 1, "Manager should have 1 task after unregistering task1."
    assert manager._tasks.get(task1_id) is None, "Task1 should be removed."
    assert manager._tasks.get(task2_id) is dummy_task2, "Task2 should still be present."

    await manager.unregister_task(task2_id)
    assert len(manager._tasks) == 0, "Manager should be empty after unregistering task2."
    assert manager._tasks.get(task2_id) is None, "Task2 should be removed."

    # Clean up tasks
    await dummy_task1
    await dummy_task2

# Ensure that a task cancelled externally is handled correctly by ActiveTaskManager
@pytest.mark.asyncio
async def test_cancel_externally_then_manager_cancel(manager: ActiveTaskManager):
    conversation_id = "external_cancel_007"
    task = asyncio.create_task(long_running_cancellable_coro())
    await manager.register_task(conversation_id, task)

    # Cancel the task directly (externally to the manager's cancel_task method)
    task.cancel()
    
    with pytest.raises(asyncio.CancelledError):
        await task # Await it to ensure the cancellation is processed by the event loop
    
    assert task.cancelled() is True

    # Now try to cancel it via the manager
    # cancel_task() should still return True because the task is found.
    # The underlying task.cancel() will return False as it's already cancelled, but our method doesn't reflect that.
    manager_cancel_result = await manager.cancel_task(conversation_id)
    assert manager_cancel_result is True 
    
    assert task.cancelled() is True # Still cancelled

    await manager.unregister_task(conversation_id)
    assert conversation_id not in manager._tasks

# Test unregistering a task that was cancelled externally
@pytest.mark.asyncio
async def test_unregister_externally_cancelled_task(manager: ActiveTaskManager):
    conversation_id = "unregister_external_cancel_008"
    task = asyncio.create_task(long_running_cancellable_coro())
    await manager.register_task(conversation_id, task)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    
    assert task.cancelled() is True

    await manager.unregister_task(conversation_id)
    assert conversation_id not in manager._tasks
    # The task is already cancelled, unregistering should not change its state.
    assert task.cancelled() is True

# Test unregistering a task that completed on its own
@pytest.mark.asyncio
async def test_unregister_self_completed_task(manager: ActiveTaskManager):
    conversation_id = "unregister_self_completed_009"
    task = asyncio.create_task(dummy_coro(0.01))
    await manager.register_task(conversation_id, task)

    await task # Ensure completion
    
    assert task.done() is True
    assert not task.cancelled()

    await manager.unregister_task(conversation_id)
    assert conversation_id not in manager._tasks
    assert task.done() is True
    assert not task.cancelled()

# Test that logger messages are emitted (requires capturing logs or mocking logger)
# This is more advanced and might require a separate fixture for log capturing.
# For simplicity, we'll skip direct log testing here unless specifically required.

# Consider thread-safety if ActiveTaskManager were to be used across threads,
# but for asyncio, the lock handles concurrent access from different coroutines.
# Standard pytest-asyncio runs tests in separate event loops, so direct concurrency
# testing for the lock needs careful setup (e.g. using threads to interact with the
# same manager instance, which is generally not how asyncio objects are used).

# Dummy test to ensure pytest runs
def test_placeholder():
    assert True
