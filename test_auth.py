"""
Test script for WikiWare authentication system.
"""

import asyncio
from src.services.user_service import UserService
from src.models.user import UserRegistration

async def test_auth():
    """Test user registration and authentication."""
    print("Testing WikiWare authentication system...")
    
    # Test user registration
    user_data = UserRegistration(
        username="testuser",
        email="test@example.com",
        password="testpassword123"
    )
    
    print("Registering test user...")
    user = await UserService.create_user(user_data)
    
    if user:
        print("User registered successfully!")
        print(f"Username: {user['username']}")
        print(f"Email: {user['email']}")
        print("Password hash (first 20 chars):", user['password_hash'][:20])
        
        # Test authentication
        print("\nTesting authentication...")
        authenticated_user = await UserService.authenticate_user("testuser", "testpassword123")
        
        if authenticated_user:
            print("Authentication successful!")
            print(f"Authenticated user: {authenticated_user['username']}")
        else:
            print("Authentication failed!")
    else:
        print("User registration failed!")

if __name__ == "__main__":
    asyncio.run(test_auth())
