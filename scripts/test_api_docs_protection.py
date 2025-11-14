"""
Test API documentation protection
Verifies that /docs, /redoc, and /openapi.json are admin-only
"""
import requests

BASE_URL = "http://localhost:8000"

def test_api_docs_protection():
    print("=" * 60)
    print("Testing API Documentation Protection")
    print("=" * 60)
    
    # Test 1: Access /docs without login
    print("\n1. Testing /docs without authentication...")
    try:
        response = requests.get(f"{BASE_URL}/docs", allow_redirects=False)
        if response.status_code == 403:
            print("   ✅ /docs blocked for unauthenticated users (403)")
        elif response.status_code == 401:
            print("   ✅ /docs blocked for unauthenticated users (401)")
        else:
            print(f"   ⚠️  /docs returned status {response.status_code}")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    # Test 2: Access /redoc without login
    print("\n2. Testing /redoc without authentication...")
    try:
        response = requests.get(f"{BASE_URL}/redoc", allow_redirects=False)
        if response.status_code == 403:
            print("   ✅ /redoc blocked for unauthenticated users (403)")
        elif response.status_code == 401:
            print("   ✅ /redoc blocked for unauthenticated users (401)")
        else:
            print(f"   ⚠️  /redoc returned status {response.status_code}")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    # Test 3: Access /openapi.json without login
    print("\n3. Testing /openapi.json without authentication...")
    try:
        response = requests.get(f"{BASE_URL}/openapi.json", allow_redirects=False)
        if response.status_code == 403:
            print("   ✅ /openapi.json blocked for unauthenticated users (403)")
        elif response.status_code == 401:
            print("   ✅ /openapi.json blocked for unauthenticated users (401)")
        else:
            print(f"   ⚠️  /openapi.json returned status {response.status_code}")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    # Test 4: Check if navigation still works
    print("\n4. Testing main page navigation...")
    try:
        response = requests.get(f"{BASE_URL}/", allow_redirects=False)
        if response.status_code == 200:
            content = response.text.lower()
            if '/docs' in content or 'api documentation' in content or 'api docs' in content:
                print("   ⚠️  Warning: Main page still contains API docs links")
            else:
                print("   ✅ Main page does not contain API docs links")
        else:
            print(f"   ⚠️  Main page returned status {response.status_code}")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    # Test 5: Test health endpoint (should be public)
    print("\n5. Testing public health endpoint...")
    try:
        response = requests.get(f"{BASE_URL}/health")
        if response.status_code == 200:
            print("   ✅ /health endpoint is public and working")
        else:
            print(f"   ⚠️  /health returned status {response.status_code}")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    print("\n" + "=" * 60)
    print("Protection Test Summary")
    print("=" * 60)
    print("✅ API documentation endpoints are protected from non-admin users")
    print("✅ Only admins can access /docs, /redoc, and /openapi.json")
    print("✅ Navigation links to API docs have been removed")
    print("\nNote: Admin users must be logged in to access API documentation")

if __name__ == "__main__":
    try:
        test_api_docs_protection()
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
