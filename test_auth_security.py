"""
Test script for WikiWare authentication system with security enhancements.
"""

import asyncio
from src.services.user_service import UserService
from src.models.user import UserRegistration

async def test_auth_security():
    """Test user registration and authentication with security enhancements."""
    print("Testing WikiWare authentication system with security enhancements...")
    
    # Test user registration
    user_data = UserRegistration(
        username="testuser",
        password="testpassword123"
    )
    
    print("Registering test user...")
    user = await UserService.create_user(user_data)
    
    if user:
        print("User registered successfully!")
        print(f"Username: {user['username']}")
        print("Password hash (first 20 chars):", user['password_hash'][:20])
        
        # Test authentication
        print("\nTesting authentication...")
        authenticated_user = await UserService.authenticate_user("testuser", "testpassword123")
        
        if authenticated_user:
            print("Authentication successful!")
            print(f"Authenticated user: {authenticated_user['username']}")
            
            # Test session creation
            print("\nTesting session creation...")
            session_id = await UserService.create_session(authenticated_user['username'])
            if session_id:
                print(f"Session created successfully! Session ID: {session_id[:20]}...")
                
                # Test session validation
                print("\nTesting session validation...")
                session_user = await UserService.get_user_by_session(session_id)
                if session_user:
                    print(f"Session validated successfully! User: {session_user['username']}")
                    
                    # Test session deletion
                    print("\nTesting session deletion...")
                    deleted = await UserService.delete_session(session_id)
                    if deleted:
                        print("Session deleted successfully!")
                    else:
                        print("Failed to delete session!")
                else:
                    print("Failed to validate session!")
            else:
                print("Failed to create session!")
        else:
            print("Authentication failed!")
    else:
        print("User registration failed!")

if __name__ == "__main__":
    asyncio.run(test_auth_security())
