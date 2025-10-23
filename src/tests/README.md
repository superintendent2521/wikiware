# WikiWare Integration Tests

This directory contains integration tests for WikiWare that interact with a running dev server.

## Test Coverage

The tests cover the following user workflows:

1. **Server Availability Check** - Verifies the dev server is running
2. **Account Creation** - Creates a new user account via the registration form
3. **User Login** - Logs in as a created user
4. **Page Creation** - Creates a new wiki page
5. **Page Viewing** - Views an existing wiki page

## Running the Tests

### Prerequisites

1. Start the dev server:
   ```bash
   python index.py
   ```
   The server should be running on `http://0.0.0.0:8000`

2. Ensure testing dependencies are installed:
   ```bash
   pip install pytest pytest-asyncio requests
   ```

### Running Tests

Run the tests using pytest:

```bash
# Run all tests
pytest src/tests/

# Run with verbose output
pytest src/tests/ -v

# Run with coverage
pytest src/tests/ --cov=src/tests

# Run only specific test
pytest src/tests/test_user_and_pages.py::test_login
```

## Test Structure

- `test_client` - A pytest fixture that provides a requests session with cookie persistence
- `test_server_available` - Checks if the dev server is running
- `test_account_creation` - Tests user registration
- `test_login` - Tests user authentication
- `test_page_creation` - Tests creating wiki pages
- `test_page_view` - Tests viewing wiki pages

## Notes

- Tests use unique usernames based on timestamps to avoid conflicts
- Tests require a running dev server on `http://0.0.0.0:8000`
- Session cookies are preserved across requests to maintain logged-in state
- Tests may create test pages that persist in the database
