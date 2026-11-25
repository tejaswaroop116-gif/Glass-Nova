# Contributing to Glass Nova

Thank you for your interest in contributing to **Glass Nova**!

## Development Setup (Windows 10/11)

1. Fork the repository and clone your fork:
   ```powershell
   git clone https://github.com/tejaswaroop30/Glass-Nova.git
   cd Glass-Nova
   ```
2. Create a Python 3.11–3.12 virtual environment and install dependencies:
   ```powershell
   py -3 -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` if you want to test online Gemini Live features locally (never commit `.env`).

## Security & Architecture Rules

- **Preserve fail-closed permissions**: Never bypass `permissions.request_permission()` or `_check_perm()`. Sensitive actions (`file_delete`, `form_purchase`, `continuous_screen`) must always require explicit user confirmation.
- **No secrets or personal paths**: Never commit API keys, `.env` files, tokens, or personal local file paths.
- **Keep tools allow-listed**: Any new tool added to `pc_assistant.py` must have a matching schema in `nova_action_router.py` (`ACTION_SCHEMAS`).
- **Keep changes focused**: Avoid unrelated UI redesigns or unnecessary third-party dependencies.

## Running Tests Before Opening a Pull Request

All 5 test suites run automatically in GitHub Actions (`.github/workflows/tests.yml`). Run them locally before submitting a PR:

```powershell
.venv\Scripts\python.exe -B test_security_audit.py -v
.venv\Scripts\python.exe -B test_nus_feature_pack.py -v
.venv\Scripts\python.exe -B test_screen_interaction.py -v
.venv\Scripts\python.exe -B test_upgrade_integration.py
.venv\Scripts\python.exe -B test_pipeline_e2e.py
```

## Opening a Pull Request

1. Create a descriptive branch (`git checkout -b fix/short-description`).
2. Commit your changes with clear commit messages.
3. Push to your fork and open a pull request explaining what changed and how it was tested.
