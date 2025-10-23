import pytest
import requests
import re
import time
from urllib.parse import quote

BASE_URL = 'http://127.0.0.1:8000'
DEFAULT_PASSWORD = 'password123'


def fetch_csrf_token(session: requests.Session, path: str) -> str:
    """Fetch CSRF token from form pages and ensure cookie is stored on the session."""
    response = session.get(f'{BASE_URL}{path}', allow_redirects=True)
    response.raise_for_status()
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match, f'CSRF token not found on {path}'
    return match.group(1)


def build_page_url(title: str, branch: str = "main") -> str:
    """Construct a properly encoded URL for viewing a page."""
    encoded_title = quote(title, safe="")
    base_url = f'{BASE_URL}/page/{encoded_title}'
    if branch and branch != "main":
        return f'{base_url}?branch={quote(branch, safe="")}'
    return base_url


@pytest.fixture(scope='module')
def user_credentials():
    """Provide shared credentials for authentication tests."""
    timestamp = str(int(time.time()))
    return {
        'username': f'test_user_{timestamp}',
        'password': DEFAULT_PASSWORD,
    }

@pytest.fixture(scope='module')
def test_client():
    """
    Test client fixture that connects to the running dev server.
    This assumes the server is already running on 0.0.0.0:8000
    """
    # Create a session that will persist cookies across requests
    session = requests.Session()
    
    # Set default timeout for requests
    session.timeout = 30
    
    yield session

def test_server_available(test_client):
    """
    Test to verify the server is available before running other tests.
    """
      # Wait for a moment to not hit the server real hard (race conditions?)
    try:
        response = test_client.get(f'{BASE_URL}/')
        assert response.status_code in [200, 301, 302], f"Server not available: {response.status_code}"
    except requests.exceptions.ConnectionError:
        pytest.fail("Server not available at http://127.0.0.1:8000. Make sure the dev server is running.")

def test_account_creation(test_client, user_credentials):
    """
    Test account creation with a unique username
    """
    # Fetch CSRF token before submitting registration form
      # Wait for a moment to not hit the server real hard (race conditions?)
    csrf_token = fetch_csrf_token(test_client, "/register")
    
    # Try registration (this is for web form, not API)
    response = test_client.post(f'{BASE_URL}/register', data={
        'username': user_credentials['username'],
        'password': user_credentials['password'],
        'confirm_password': user_credentials['password'],
        'csrf_token': csrf_token,
    }, allow_redirects=True)
    
    # Check if registration was successful (redirect to home page)
    assert response.status_code == 200, f"Registration failed: {response.status_code}"
    
    # Check if we were redirected or if the page contains expected content
    content = response.text.lower()
    assert "home" in content or "welcome" in content, "Expected home page after registration"

def test_login(test_client, user_credentials):
    """
    Test login with the created user
    """
    # Attempt login using the credentials created during registration
      # Wait for a moment to not hit the server real hard (race conditions?)
    test_client.cookies.clear()  # Ensure a clean session for the login test
    csrf_token = fetch_csrf_token(test_client, "/login")

    # Try login
    response = test_client.post(f'{BASE_URL}/login', data={
        'username': user_credentials['username'],
        'password': user_credentials['password'],
        'csrf_token': csrf_token,
        'next': '/',
    }, allow_redirects=True)
    
    # Check if login was successful
    assert response.status_code == 200, f"Login failed: {response.status_code}"
    
    # Check if we were redirected to home or see expected content
    content = response.text.lower()
    assert "home" in content, "Expected home page after login"
    
    # Check if session cookie was set
    assert 'user_session' in test_client.cookies, "Session cookie not set after login"

def test_page_creation(test_client, user_credentials):
    """
    Test page creation
    This test requires authentication from login
    """

    # First, try to login
    test_client.cookies.clear()
    csrf_token = fetch_csrf_token(test_client, "/login")
    login_response = test_client.post(f'{BASE_URL}/login', data={
        'username': user_credentials['username'],
        'password': user_credentials['password'],
        'csrf_token': csrf_token,
        'next': '/',
    }, allow_redirects=False)
    
    # If login gives a redirect, follow it
    if login_response.status_code in [301, 302, 303]:
        redirect_location = login_response.headers.get('Location')
        if redirect_location:
            redirect_url = f'{BASE_URL}{redirect_location}' if redirect_location.startswith('/') else redirect_location
            test_client.get(redirect_url, allow_redirects=False)
    
    # Ensure we have a session cookie
    assert 'user_session' in test_client.cookies, "Failed to login"
    
    # Now try to create a page
    page_title = f"Test Page {int(time.time())}"
    edit_csrf_token = fetch_csrf_token(test_client, f"/edit/{page_title}")
    response = test_client.post(f'{BASE_URL}/edit/{page_title}', data={
        'content': '# Test Page Content\n\nThis is a test page created by automated tests.',
        'edit_summary': 'Test page creation',
        'edit_permission': 'everybody',
        'allowed_users': '',
        'csrf_token': edit_csrf_token,
    }, allow_redirects=False)
    
    # Should redirect to the page after creation
    assert response.status_code in [301, 302, 303], f"Page creation failed: {response.status_code}"
    
    # Get the redirected URL
    page_url = response.headers.get('Location')
    assert page_url, "Page creation response missing redirect location"
    full_page_url = f'{BASE_URL}{page_url}' if page_url.startswith('/') else page_url
    
    
    # Verify the page was created by visiting it
    page_response = test_client.get(full_page_url)
    assert page_response.status_code == 200, f"Page view failed: {page_response.status_code}"
    
    # Check if our content is in the page
    content = page_response.text.lower()
    assert 'test page content' in content, "Page content not found"

def test_page_view(test_client, user_credentials):
    """
    Test viewing an existing page
    """
      # Wait to avoid hammering the server
    page_title = f"View Test Page {int(time.time())}"

    # Ensure clean authentication state
    test_client.cookies.clear()
    csrf_token = fetch_csrf_token(test_client, "/login")
    login_response = test_client.post(f'{BASE_URL}/login', data={
        'username': user_credentials['username'],
        'password': user_credentials['password'],
        'csrf_token': csrf_token,
        'next': '/',
    }, allow_redirects=False)

    if login_response.status_code in [301, 302, 303]:
        redirect_location = login_response.headers.get('Location')
        if redirect_location:
            redirect_url = f'{BASE_URL}{redirect_location}' if redirect_location.startswith('/') else redirect_location
            test_client.get(redirect_url, allow_redirects=False)

    if 'user_session' not in test_client.cookies:
        pytest.fail("Could not login to create test page")

    # Create the page that we are going to view
    edit_csrf_token = fetch_csrf_token(test_client, f"/edit/{page_title}")
    create_response = test_client.post(f'{BASE_URL}/edit/{page_title}', data={
        'content': 'View Page Content\n\nThis page is for testing the view functionality.',
        'edit_summary': 'Create test view page',
        'edit_permission': 'everybody',
        'allowed_users': '',
        'csrf_token': edit_csrf_token,
    }, allow_redirects=False)

    assert create_response.status_code in [301, 302, 303], f"Failed to create page for view test: {create_response.status_code}"

    redirect_location = create_response.headers.get('Location')
    if redirect_location:
        page_url_to_check = f'{BASE_URL}{redirect_location}' if redirect_location.startswith('/') else redirect_location
    else:
        page_url_to_check = build_page_url(page_title)

    

    # Now test viewing the page
    page_response = test_client.get(page_url_to_check)
    assert page_response.status_code == 200, f"Page view failed: {page_response.status_code}"

    # Check if page contains the expected content
    content = page_response.text.lower()
    assert 'view page content' in content, "Page content not found in view"
