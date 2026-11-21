"""End-to-end test verifying the permission-to-capture-to-response pipeline:
Verifies that granting permission triggers the operation immediately without deadlock or freeze.
"""
import sys, time, json, threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import permissions
from permissions import PermissionManager
from screen_agent import analyze_screen

def test_permission_pipeline():
    print("Testing permission pipeline...")
    pm = PermissionManager()
    pm.revoke_all()

    # Simulate UI responding to prompt
    def simulate_ui_approval(category, action_desc, details):
        req_id = "test_req_123"
        print(f"  [Simulated UI] Received prompt: {action_desc} ({category})")
        # In real system, UI receives perm_request and user clicks [Allow for Session]
        time.sleep(0.1) # simulated human reaction
        print("  [Simulated UI] User clicked 'Allow for Session'")
        return "allow_session"

    pm.set_prompt_callback(simulate_ui_approval)

    # Now call analyze_screen - this runs the real screen capture and checks permissions
    start_time = time.monotonic()
    # Mock get_client for offline test if no internet
    import screen_agent
    orig_get_client = screen_agent.get_client
    class MockModel:
        def generate_content(self, model, contents, config=None):
            class MockResponse:
                text = "I analyzed your screen: You have VS Code open with Python."
            return MockResponse()
    class MockClient:
        models = MockModel()
    screen_agent._client = MockClient()

    result = analyze_screen(query="What is on the screen?")
    duration = time.monotonic() - start_time

    assert result["ok"] is True, f"analyze_screen failed: {result}"
    assert "VS Code" in result["message"]
    assert pm.is_granted("screen_view")
    print(f"Pipeline executed in {duration:.2f}s without deadlock!")
    print(f"Result: {result['message']}")
    print("Permission-to-response pipeline test PASSED!")

if __name__ == "__main__":
    test_permission_pipeline()
