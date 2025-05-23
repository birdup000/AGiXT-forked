import asyncio
import logging
from typing import Dict

logger = logging.getLogger(__name__)

class ActiveTaskManager:
    """
    Manages active asynchronous tasks associated with conversation IDs.
    """
    def __init__(self):
        """
        Initializes an empty dictionary to store tasks and an asyncio.Lock.
        """
        self._tasks: Dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        logger.info("ActiveTaskManager initialized.")

    async def register_task(self, conversation_id: str, task: asyncio.Task):
        """
        Stores the task with the given conversation_id.

        Args:
            conversation_id: The ID of the conversation.
            task: The asyncio.Task to store.
        """
        async with self._lock:
            if conversation_id in self._tasks:
                logger.warning(f"Task already registered for conversation_id: {conversation_id}. Overwriting.")
            self._tasks[conversation_id] = task
            logger.info(f"Task registered for conversation_id: {conversation_id}")

    async def unregister_task(self, conversation_id: str):
        """
        Removes the task associated with the conversation_id.

        Args:
            conversation_id: The ID of the conversation.
        """
        async with self._lock:
            if conversation_id in self._tasks:
                del self._tasks[conversation_id]
                logger.info(f"Task unregistered for conversation_id: {conversation_id}")
            else:
                logger.warning(f"No task found for unregistration with conversation_id: {conversation_id}")

    async def cancel_task(self, conversation_id: str) -> bool:
        """
        Cancels the task associated with the conversation_id.

        Args:
            conversation_id: The ID of the conversation.

        Returns:
            True if the task was found and cancel was called, False otherwise.
        """
        async with self._lock:
            task = self._tasks.get(conversation_id)
            if task:
                try:
                    task.cancel()
                    logger.info(f"Task cancellation initiated for conversation_id: {conversation_id}")
                    # Give the task a chance to process the cancellation
                    await asyncio.sleep(0)
                    # Optionally, remove the task from the registry after cancellation
                    # del self._tasks[conversation_id]
                    # logger.info(f"Cancelled task removed from registry for conversation_id: {conversation_id}")
                    return True
                except Exception as e:
                    logger.error(f"Error cancelling task for conversation_id {conversation_id}: {e}")
                    return False
            else:
                logger.warning(f"No task found for cancellation with conversation_id: {conversation_id}")
                return False
