"""
Test script to verify the update system is properly configured
"""
import sys
import asyncio

def test_version_import():
    """Test that version can be imported from multiple locations"""
    print("Testing version imports...")
    
    try:
        from app import VERSION, BUILD_DATE
        print(f"✓ app.VERSION: {VERSION}")
        print(f"✓ app.BUILD_DATE: {BUILD_DATE}")
    except ImportError as e:
        print(f"✗ Failed to import from app: {e}")
        return False
    
    try:
        from app.core.version import VERSION as V2
        print(f"✓ app.core.version.VERSION: {V2}")
    except ImportError as e:
        print(f"✗ Failed to import from app.core.version: {e}")
        return False
    
    return True


async def test_update_check():
    """Test that update check function works"""
    print("\nTesting update check functionality...")
    
    try:
        from app.core.updates import check_for_updates, get_current_version
        
        current = get_current_version()
        print(f"✓ Current version: {current}")
        
        # Note: This will fail if UPDATE_CHECK_URL is not reachable
        # but the function should handle it gracefully
        result = await check_for_updates()
        print(f"✓ Update check executed")
        print(f"  - Current version: {result.get('current_version')}")
        print(f"  - Update available: {result.get('update_available')}")
        
        return True
    except Exception as e:
        print(f"✗ Update check failed: {e}")
        return False


def test_system_routes():
    """Test that system routes are properly defined"""
    print("\nTesting system routes...")
    
    try:
        from app.api.routes import system
        print(f"✓ System routes module imported")
        print(f"✓ Router prefix: {system.router.prefix}")
        
        routes = [route.path for route in system.router.routes]
        print(f"✓ Available endpoints:")
        for route in routes:
            print(f"  - {route}")
        
        return True
    except Exception as e:
        print(f"✗ System routes test failed: {e}")
        return False


def test_config():
    """Test that configuration is properly loaded"""
    print("\nTesting configuration...")
    
    try:
        from app.core.version import (
            VERSION, 
            BUILD_DATE, 
            UPDATE_CHECK_URL,
            ENABLE_AUTO_UPDATE_CHECK,
            UPDATE_CHECK_INTERVAL_HOURS
        )
        
        print(f"✓ VERSION: {VERSION}")
        print(f"✓ BUILD_DATE: {BUILD_DATE}")
        print(f"✓ UPDATE_CHECK_URL: {UPDATE_CHECK_URL}")
        print(f"✓ ENABLE_AUTO_UPDATE_CHECK: {ENABLE_AUTO_UPDATE_CHECK}")
        print(f"✓ UPDATE_CHECK_INTERVAL_HOURS: {UPDATE_CHECK_INTERVAL_HOURS}")
        
        return True
    except Exception as e:
        print(f"✗ Configuration test failed: {e}")
        return False


async def main():
    print("=" * 50)
    print("CRM Backend - Update System Verification")
    print("=" * 50)
    print()
    
    results = []
    
    # Test 1: Version imports
    results.append(("Version Imports", test_version_import()))
    
    # Test 2: Configuration
    results.append(("Configuration", test_config()))
    
    # Test 3: System routes
    results.append(("System Routes", test_system_routes()))
    
    # Test 4: Update check
    results.append(("Update Check", await test_update_check()))
    
    # Summary
    print("\n" + "=" * 50)
    print("Test Summary")
    print("=" * 50)
    
    passed = 0
    failed = 0
    
    for test_name, result in results:
        status = "✓ PASSED" if result else "✗ FAILED"
        print(f"{test_name:20} {status}")
        if result:
            passed += 1
        else:
            failed += 1
    
    print()
    print(f"Total: {passed + failed} tests")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print()
    
    if failed == 0:
        print("✓ All tests passed! Update system is ready.")
        return 0
    else:
        print(f"✗ {failed} test(s) failed. Please review the errors above.")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
