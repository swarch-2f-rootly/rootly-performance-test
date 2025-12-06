#!/usr/bin/env python3
"""
Clear Cache Script for Rootly Analytics Backend
This script clears all cached data from Redis using the analytics cache clear endpoint.
"""

import requests
import json
import sys
from pathlib import Path


class CacheCleaner:
    """Utility class for clearing analytics cache."""
    
    def __init__(self, base_url: str = "http://127.0.0.1", port: int = 8080):
        """
        Initialize cache cleaner with API endpoint configuration.
        
        Args:
            base_url: Base URL for the analytics service
            port: Port number for the analytics service
        """
        self.base_url = base_url
        self.port = port
        self.endpoint = f"{base_url}:{port}/api/v1/analytics/cache/clear"
    
    def load_config(self, config_file: str = "config.json") -> dict:
        """
        Load configuration from JSON file.
        
        Args:
            config_file: Path to configuration file
            
        Returns:
            Configuration dictionary
        """
        try:
            config_path = Path(__file__).parent / config_file
            with open(config_path, 'r') as f:
                config = json.load(f)
                
            # Extract base URL and port from config if available
            if 'base_url' in config:
                self.base_url = config['base_url']
                
            # Check if auth credentials are available for getting token
            if 'auth_credentials' in config:
                auth_port = config['auth_credentials'].get('login_port', 8080)
                if auth_port != self.port:
                    self.port = auth_port
                    
            # Update endpoint URL
            self.endpoint = f"{self.base_url}:{self.port}/api/v1/analytics/cache/clear"
            
            return config
            
        except FileNotFoundError:
            print(f"Warning: Config file '{config_file}' not found. Using default settings.")
            return {}
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON in config file: {e}")
            return {}
    
    def get_auth_token(self, config: dict) -> str:
        """
        Get authentication token from auth service.
        
        Args:
            config: Configuration dictionary containing auth credentials
            
        Returns:
            JWT token string or None if authentication fails
        """
        if 'auth_credentials' not in config:
            print("No authentication credentials found in config.")
            return None
            
        auth_creds = config['auth_credentials']
        login_endpoint = auth_creds.get('login_endpoint', '/api/v1/auth/login')
        login_port = auth_creds.get('login_port', 8080)
        
        login_url = f"{self.base_url}:{login_port}{login_endpoint}"
        
        login_data = {
            "email": auth_creds.get('email'),
            "password": auth_creds.get('password')
        }
        
        try:
            print(f"Authenticating at: {login_url}")
            response = requests.post(
                login_url,
                json=login_data,
                headers={'Content-Type': 'application/json'},
                timeout=30
            )
            
            if response.status_code == 200:
                auth_response = response.json()
                token = auth_response.get('access_token')
                if token:
                    print("✓ Authentication successful")
                    return token
                else:
                    print("Error: No access_token in auth response")
                    return None
            else:
                print(f"Authentication failed: {response.status_code}")
                print(f"Response: {response.text}")
                return None
                
        except requests.RequestException as e:
            print(f"Authentication request failed: {e}")
            return None
    
    def clear_cache(self, auth_token: str = None) -> bool:
        """
        Clear all cached data from Redis.
        
        Args:
            auth_token: JWT token for authentication (optional)
            
        Returns:
            True if cache cleared successfully, False otherwise
        """
        headers = {'Content-Type': 'application/json'}
        
        if auth_token:
            headers['Authorization'] = f'Bearer {auth_token}'
        
        try:
            print(f"Clearing cache at: {self.endpoint}")
            print(f"Using authentication: {'Yes' if auth_token else 'No'}")
            
            response = requests.post(
                self.endpoint,
                headers=headers,
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                print("✓ Cache cleared successfully!")
                print(f"  Status: {result.get('status')}")
                print(f"  Message: {result.get('message')}")
                print(f"  Timestamp: {result.get('timestamp')}")
                return True
            elif response.status_code == 401:
                print("✗ Authentication required or token invalid")
                print("  Make sure you have valid credentials in config.json")
                return False
            elif response.status_code == 503:
                print("✗ Cache service unavailable")
                print("  The Redis cache service may be disabled or not configured")
                return False
            else:
                print(f"✗ Cache clear failed: HTTP {response.status_code}")
                print(f"Response: {response.text}")
                return False
                
        except requests.RequestException as e:
            print(f"✗ Request failed: {e}")
            print("  Make sure the analytics service is running")
            return False


def main():
    """Main function to run cache clearing script."""
    print("=" * 60)
    print("Rootly Analytics Backend - Cache Clear Utility")
    print("=" * 60)
    
    # Initialize cache cleaner
    cleaner = CacheCleaner()
    
    # Load configuration
    config = cleaner.load_config()
    
    # Get authentication token if credentials are available
    auth_token = cleaner.get_auth_token(config)
    
    if not auth_token and config.get('auth_credentials'):
        print("\nWarning: Authentication failed but continuing without token...")
        print("The endpoint may require authentication.\n")
    
    # Clear the cache
    success = cleaner.clear_cache(auth_token)
    
    if success:
        print("\n✓ Cache clearing completed successfully!")
        sys.exit(0)
    else:
        print("\n✗ Cache clearing failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()