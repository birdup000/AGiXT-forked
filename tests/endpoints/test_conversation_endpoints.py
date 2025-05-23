import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock

# Ensure the app is imported correctly.
# If 'agixt.app' is the module where your FastAPI 'app' instance is, this is correct.
# You might need to adjust the import path based on your project structure.
from agixt.app import app, task_manager  # Assuming task_manager is globally accessible here for patching
from Models import ResponseMessage # Assuming ResponseMessage is in your Models

# If your AGiXT app instance is named something else or located elsewhere, adjust accordingly.
client = TestClient(app)

# Mock the verify_api_key dependency for all tests in this module
# This is a common pattern if you don't want to deal with actual API key verification during these tests.
# You might need to adjust the path to where `verify_api_key` is actually located and used by your router.
# For example, if it's in `ApiClient.verify_api_key` and your endpoint uses it directly.
# If it's a dependency injected into the router, the setup might be different.
# For now, let's assume a simple patch that bypasses it.

@pytest.fixture(autouse=True)
def bypass_auth():
    with patch("ApiClient.verify_api_key", new_callable=AsyncMock) as mock_verify_api_key:
        # Define what the mocked verify_api_key should return.
        # Often, it's a dummy user object or just None if the endpoint doesn't use the return value.
        mock_verify_api_key.return_value = {"email": "test@example.com"} # Or whatever your verify_api_key returns
        yield

@pytest.mark.asyncio
async def test_stop_endpoint_success():
    conversation_id = "test_stop_success_conv"
    
    # Patch the global task_manager instance used by the endpoint
    with patch('agixt.app.task_manager.cancel_task', new_callable=AsyncMock) as mock_cancel_task:
        mock_cancel_task.return_value = True # Simulate successful cancellation
        
        # Provide a dummy token for authorization header if your endpoint expects it,
        # even if verify_api_key is bypassed.
        headers = {"Authorization": "Bearer dummytoken"}
        response = client.post(f"/v1/conversation/{conversation_id}/stop", headers=headers)
        
        assert response.status_code == 200
        json_response = response.json()
        assert json_response["message"] == f"Successfully requested to stop generation for conversation {conversation_id}."
        mock_cancel_task.assert_called_once_with(conversation_id)

@pytest.mark.asyncio
async def test_stop_endpoint_task_not_found():
    conversation_id = "test_stop_not_found_conv"
    
    with patch('agixt.app.task_manager.cancel_task', new_callable=AsyncMock) as mock_cancel_task:
        mock_cancel_task.return_value = False # Simulate task not found or already cancelled
        
        headers = {"Authorization": "Bearer dummytoken"}
        response = client.post(f"/v1/conversation/{conversation_id}/stop", headers=headers)
        
        assert response.status_code == 200
        json_response = response.json()
        assert json_response["message"] == f"No active generation found for conversation {conversation_id}, or it might have already completed or been cancelled."
        mock_cancel_task.assert_called_once_with(conversation_id)

@pytest.mark.asyncio
async def test_stop_endpoint_exception_in_manager():
    conversation_id = "test_stop_exception_conv"
    
    with patch('agixt.app.task_manager.cancel_task', new_callable=AsyncMock) as mock_cancel_task:
        mock_cancel_task.side_effect = Exception("Manager internal error") # Simulate an unexpected error in the manager
        
        headers = {"Authorization": "Bearer dummytoken"}
        response = client.post(f"/v1/conversation/{conversation_id}/stop", headers=headers)
        
        assert response.status_code == 500
        json_response = response.json()
        assert json_response["detail"] == f"An error occurred while trying to stop generation for conversation {conversation_id}."
        mock_cancel_task.assert_called_once_with(conversation_id)

# Example of how a test for a protected endpoint might look if verify_api_key is more complex
# This is just for illustration if the simple patch above is not sufficient.
@pytest.mark.asyncio
async def test_stop_endpoint_auth_dependency_example(mocker):
    # More complex mocking if your dependency is, for example, a class or has more structure
    # This assumes `verify_api_key` is a function that might be part of a class or directly imported
    # and used in `Depends(verify_api_key)`.
    
    # If `verify_api_key` is directly in `ApiClient` module:
    mocked_verify = mocker.patch("ApiClient.verify_api_key", new_callable=AsyncMock)
    mocked_verify.return_value = {"email": "test_user@example.com"} # Simulate successful auth

    conversation_id = "auth_test_conv"
    with patch('agixt.app.task_manager.cancel_task', new_callable=AsyncMock, return_value=True):
        headers = {"Authorization": "Bearer sometoken"} # Token is passed but verify_api_key is mocked
        response = client.post(f"/v1/conversation/{conversation_id}/stop", headers=headers)
        assert response.status_code == 200
        # Further assertions as in test_stop_endpoint_success

# Placeholder for full integration test - to be developed if needed
# This test would not mock task_manager.cancel_task but would mock the AI provider
# to simulate cancellation. It's complex and deferred.
@pytest.mark.asyncio
async def test_full_cancellation_flow(mocker):
    conversation_id = "full_cancel_flow_conv"
    
    # 1. Mock the AI provider's inference method
    #    This needs to be the actual path to the inference method called by AGiXT.inference
    #    Let's assume it's within a specific provider class or a general provider interface.
    #    For this example, I'll assume a generic path. You'll need to adjust this
    #    to match where `self.PROVIDER.inference` in `Agent.py` actually points to.
    #    A common pattern is to have a base provider and specific implementations.
    #    If `Agent.PROVIDER` is an instance of `agixt.providers.OpenAIProvider` (example),
    #    the path would be `agixt.providers.OpenAIProvider.inference`.
    #    If it's more dynamic, this mock might need to be more sophisticated or target
    #    the `Agent.inference` method directly, but that's higher level than ideal.
    #    Let's assume a common base class or a specific one for now.
    #    The target for the mock should be where the actual `await` for the LLM call happens.
    
    # This is a placeholder path. You MUST adjust this to the actual provider's inference method path.
    # Example: 'agixt.Providers.Providers.inference' if 'Providers' is the class and 'inference' the method.
    # Or, if you have specific providers like 'agixt.providers.openai.OpenAIProvider.inference'.
    # For the sake of this example, let's assume a general path that might need adjustment:
    mocked_provider_inference = mocker.patch('agixt.Providers.Providers.inference', new_callable=AsyncMock)

    long_sleep_event = asyncio.Event()

    async def long_running_inference_mock(*args, **kwargs):
        try:
            # Simulate a long-running operation
            print("Mocked inference started, waiting for cancellation...")
            await asyncio.wait_for(long_sleep_event.wait(), timeout=30) # Wait for event or timeout
            print("Mocked inference finished normally (should not happen in this test)")
            return "Mocked LLM response (not cancelled)"
        except asyncio.CancelledError:
            print("Mocked inference was cancelled.")
            raise # Important to re-raise
        except asyncio.TimeoutError:
            print("Mocked inference timed out (test error).")
            return "Mocked LLM response (timeout)"


    mocked_provider_inference.side_effect = long_running_inference_mock

    # Prepare the chat completion request payload
    chat_payload = {
        "messages": [{"role": "user", "content": "Tell me a very long story that takes ages to generate."}],
        # Add other required fields for your chat completions endpoint
        # e.g., agent_name, prompt_name, etc., if they are part of the model
        # For simplicity, assuming a basic payload. Adjust as per your ChatCompletions model.
        # The AGiXT class will be initialized within the endpoint, using conversation_name from the path.
    }
    
    headers = {"Authorization": "Bearer dummytoken"} # Assuming auth is bypassed by fixture

    async def run_chat_completion():
        # The AGiXT class in XT.py uses self.conversation_id which is part of the AGiXT instance.
        # The endpoint /v1/chat/{agent_name}/{conversation_id} will set this up.
        # Or if your chat endpoint is different, adjust accordingly.
        # Let's assume a chat endpoint that takes conversation_id in the path.
        # A common pattern for AGiXT might be POST /v1/chat/{agent_name}/{conversation_id} or similar.
        # For this example, let's use a hypothetical endpoint structure.
        # The actual endpoint that calls XT.chat_completions needs to be targeted.
        # Let's use the one from Completions.py: /v1/chat/completions
        # This endpoint might implicitly create a conversation_id or expect one.
        # For our XT.chat_completions, it derives conversation_id from self.conversation_id,
        # which is set up when AGiXT instance is created with a conversation_name.
        # The endpoint /v1/chat/completions in `endpoints/Completions.py` seems to create an AGiXT instance.
        # It uses `prompt.user` as `conversation_name`. Let's ensure our payload reflects this.
        
        # The /v1/chat/completions endpoint uses `prompt.user` as the `conversation_name` to initialize AGiXT.
        # This `conversation_name` then becomes the `conversation_id` if it's a UUID, or is used to derive one.
        # For the stop mechanism to work, we need to ensure the `conversation_id` used by the stop endpoint
        # matches the one used by `ActiveTaskManager` inside `XT.chat_completions`.
        
        # To make it simpler, let's assume the `conversation_id` is explicitly part of the chat request
        # or that `prompt.user` can serve as this unique ID if it's a UUID.
        # The `XT.chat_completions` method uses `self.conversation_id`.
        # The `Completions.py` endpoint for `/v1/chat/completions` takes a `ChatCompletionRequest`
        # which has a `user: str = "USER"` field. This `user` field is used as `conversation_name` for AGiXT.
        # So, we'll set `prompt.user` to our `conversation_id`.
        
        chat_payload_for_endpoint = {
            "model": "TestAgent", # Agent name
            "messages": [{"role": "user", "content": "Tell me a very long story..."}],
            "user": conversation_id, # This will be used as conversation_name by AGiXT
            "agent_name": "TestAgent", # Ensure agent_name is part of payload if endpoint needs it
            "api_key": "dummytoken" # Ensure api_key is part of payload if endpoint needs it
            # Add any other fields your ChatCompletionRequest model requires
        }
        print(f"Client: Starting chat completion for conversation_id: {conversation_id}")
        response = client.post("/v1/chat/completions", json=chat_payload_for_endpoint, headers=headers)
        print(f"Client: Chat completion response received: {response.status_code}, {response.text}")
        return response

    # Run chat completion in a background task
    chat_task = asyncio.create_task(run_chat_completion())

    # Give the chat task a moment to start and register itself
    await asyncio.sleep(0.2) # Increased sleep to ensure task registration and mock setup
    print(f"Client: Chat task should be running. Registered tasks: {task_manager._tasks.keys()}")


    # Check if the task is registered (optional, for debugging)
    # This depends on how quickly the AGiXT instance in the endpoint registers the task.
    # It's possible the mock starts before registration if not careful.
    # The key used for registration is AGiXT's self.conversation_id.
    assert conversation_id in task_manager._tasks, "Task was not registered by chat_completions"


    # Trigger the stop endpoint
    print(f"Client: Calling stop endpoint for conversation_id: {conversation_id}")
    stop_response = client.post(f"/v1/conversation/{conversation_id}/stop", headers=headers)
    assert stop_response.status_code == 200
    assert stop_response.json()["message"] == f"Successfully requested to stop generation for conversation {conversation_id}."
    print("Client: Stop endpoint call successful.")

    # Allow the mocked inference to release if it was waiting on an event (not strictly necessary if it handles CancelledError well)
    long_sleep_event.set() 

    # Await the chat completion task, which should now be cancelled
    chat_response = await chat_task
    
    print(f"Client: Final chat response status: {chat_response.status_code}")
    print(f"Client: Final chat response content: {chat_response.text}")

    # Assert that the chat completion response indicates cancellation
    # This depends on how XT.chat_completions formats its response upon CancelledError
    assert chat_response.status_code == 200 # Or whatever status code your app returns for this case
    json_response = chat_response.json()
    
    # Check the structure of the response from XT.py for cancelled tasks
    # Expected: {"id": conversation_id, "object": "chat.completion", ..., "choices": [{"message": {"content": "..."}}]}
    assert json_response["id"] == conversation_id
    assert json_response["choices"][0]["message"]["content"] == "The AI response generation was stopped by the user."
    assert json_response["choices"][0]["finish_reason"] == "cancelled"
    
    # Ensure the mock was called
    mocked_provider_inference.assert_called_once()
    
    # Ensure the task is no longer in the manager (should be unregistered in `finally` block)
    # May need a small delay for the finally block in XT.chat_completions to execute
    await asyncio.sleep(0.01) 
    assert conversation_id not in task_manager._tasks, "Task was not unregistered after cancellation."


# Basic test to ensure the test file is picked up by pytest
def test_placeholder_conversation_endpoint():
    assert True
