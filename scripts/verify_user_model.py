"""
Verify User model fields are correctly defined
"""
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from app.models.user import User
from sqlmodel import SQLModel

print("=" * 60)
print("USER MODEL VERIFICATION")
print("=" * 60)
print()

# Check User model fields
print("User model fields:")
for field_name, field_info in User.__fields__.items():
    field_type = field_info.annotation
    is_required = field_info.is_required()
    default = field_info.default
    print(f"  {field_name:30} {field_type!s:40} Required: {is_required} Default: {default}")

print()
print("Checking for soft delete fields:")
print(f"  deleted_at exists: {'deleted_at' in User.__fields__}")
print(f"  deleted_by exists: {'deleted_by' in User.__fields__}")

# Check table columns
print()
print("SQLModel metadata table columns:")
user_table = User.__table__
for col in user_table.columns:
    print(f"  {col.name:30} {col.type!s:40} Nullable: {col.nullable}")

print()
print("=" * 60)
