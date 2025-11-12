#!/usr/bin/env python3
"""
Verify that all imports and modules are working correctly
"""

print("=" * 60)
print("CRM Backend - Import Verification")
print("=" * 60)
print()

errors = []
warnings = []

# Test 1: Core dependencies
print("1. Testing core dependencies...")
try:
    import uvicorn
    import fastapi
    import sqlalchemy
    import pydantic
    print("   ✓ Core dependencies OK")
except ImportError as e:
    errors.append(f"Core dependency missing: {e}")
    print(f"   ✗ {e}")

# Test 2: App imports
print("2. Testing app imports...")
try:
    from app.main import app
    print("   ✓ App imports OK")
except ImportError as e:
    errors.append(f"App import failed: {e}")
    print(f"   ✗ {e}")

# Test 3: Start server imports
print("3. Testing start_server.py imports...")
try:
    from app.core.network import get_local_ip, get_all_local_ips, get_public_ip
    from app.core.backup import backup_manager
    print("   ✓ Start server imports OK")
except ImportError as e:
    errors.append(f"Start server import failed: {e}")
    print(f"   ✗ {e}")

# Test 4: Database connectivity
print("4. Testing database module...")
try:
    from app.core.database import engine, get_session
    print("   ✓ Database module OK")
except ImportError as e:
    errors.append(f"Database module failed: {e}")
    print(f"   ✗ {e}")

# Test 5: Models
print("5. Testing models...")
try:
    from app.models.user import User
    from app.models.project import Project
    from app.models.task import Task
    print("   ✓ Models OK")
except ImportError as e:
    errors.append(f"Model import failed: {e}")
    print(f"   ✗ {e}")

# Test 6: Routes
print("6. Testing API routes...")
try:
    from app.api.routes import auth, users, projects, tasks
    print("   ✓ API routes OK")
except ImportError as e:
    errors.append(f"Route import failed: {e}")
    print(f"   ✗ {e}")

# Test 7: Web routes
print("7. Testing web routes...")
try:
    from app.web import routes as web_routes
    print("   ✓ Web routes OK")
except ImportError as e:
    errors.append(f"Web route import failed: {e}")
    print(f"   ✗ {e}")

# Summary
print()
print("=" * 60)
if errors:
    print("❌ VERIFICATION FAILED")
    print()
    print("Errors found:")
    for error in errors:
        print(f"  - {error}")
    print()
    print("Please install missing dependencies:")
    print("  python3 -m pip install -r requirements.txt")
else:
    print("✅ VERIFICATION PASSED")
    print()
    print("All imports are working correctly!")
    print("The 'errors' shown in VS Code are just IDE warnings,")
    print("not actual runtime errors.")
    print()
    print("To fix VS Code warnings:")
    print("  1. Press Ctrl+Shift+P")
    print("  2. Type 'Python: Select Interpreter'")
    print("  3. Choose '.venv\\Scripts\\python.exe'")
    print()
    print("Server is ready to start:")
    print("  python3 start_server.py")

print("=" * 60)
