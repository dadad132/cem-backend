# CRM Backend System

Enterprise-grade Customer Relationship Management system with project management, ticket system, email automation, and calendar integration.

## 🚀 Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run migrations
python scripts/run_all_migrations.py

# Start the server
python start_server.py
```

Visit `http://localhost:8000` to access the system.

## 📁 Project Structure

```
crm-backend/
├── app/                    # Main application code
│   ├── api/               # API routes
│   ├── core/              # Core services (email, security, database)
│   ├── models/            # Database models
│   ├── schemas/           # Pydantic schemas
│   ├── templates/         # HTML templates
│   └── web/               # Web routes
├── docs/                  # Documentation
├── migrations/            # Database migration scripts
├── scripts/               # Utility and deployment scripts
├── tests/                 # Test files
├── alembic/              # Alembic migration configs
├── backups/              # Database backups
├── logs/                 # Application logs
└── deployment/           # Deployment configurations
```

## 📚 Documentation

All documentation is in the `docs/` folder:

### Getting Started
- [Quick Start Guide](docs/QUICK_START.md)
- [Installation Guide](docs/QUICK_INSTALL.md)
- [Deployment Guide](docs/DEPLOYMENT.md)

### Features
- [Email Task Routing](docs/EMAIL_TASK_ROUTING_SYSTEM.md) - Smart email-to-task/ticket system
- [Email Reply System](docs/EMAIL_REPLY_SYSTEM_COMPLETE.md) - Automated client responses
- [Project Management](docs/PROJECT_ASSIGNMENT_SYSTEM.md)
- [Task System](docs/TASK_PERMISSIONS.md)
- [Calendar Integration](docs/GOOGLE_CALENDAR_INTEGRATION.md)
- [Backup System](docs/BACKUP_SYSTEM.md)

### Administration
- [User Management](docs/README.md)
- [Email Configuration](docs/EMAIL_TO_TICKET_GUIDE.md)
- [Update System](docs/UPDATE_SYSTEM.md)

## ✨ Key Features

### 📧 Intelligent Email System
- **Dual Routing**: Project emails create tasks, general emails create tickets
- **AI Analysis**: Auto-generates concise 3-word titles from email content
- **Smart Replies**: Technicians reply via project or main support email
- **Email Threading**: Responses link back to original tickets

### 🎫 Advanced Ticket System
- **Project-Based Visibility**: Users see only their project tickets
- **Role-Based Access**: Admins see all, technicians see assigned projects
- **Track Closures**: Records who closed each ticket
- **User Reports**: Includes tickets closed in productivity reports

### 📋 Project Management
- Task boards with drag-and-drop
- Team member assignments
- Project-specific support emails
- Archive and soft-delete support

### 📅 Calendar Integration
- Google Calendar sync
- Meeting scheduling
- Availability tracking
- User-specific colors

### 👥 User Management
- Role-based permissions (Admin, User)
- Workspace isolation
- Activity tracking and reports
- Profile pictures

## 🔧 Utilities

### Scripts Folder
- `scripts/start.sh` - Start the server
- `scripts/deploy.sh` - Deploy to production
- `scripts/run_all_migrations.py` - Run database migrations
- `scripts/backup_database.py` - Create database backup

### Migrations Folder
All database migration scripts for schema changes.

## 🔐 Security

- JWT authentication
- Password hashing with bcrypt
- Role-based access control
- Session management
- CSRF protection

## 🗄️ Database

- SQLite (default, portable)
- SQLModel ORM
- Automatic migrations
- Backup/restore support

## 📊 Reporting

- User activity reports (PDF)
- Task completion metrics
- Ticket closure tracking
- Project progress analytics

## 🌐 Deployment

See deployment guides:
- [Ubuntu Server](docs/DEPLOYMENT_UBUNTU.md)
- [Windows Server](docs/INSTALL_WINDOWS.md)
- [Docker](docker-compose.yml)

## 🆘 Support

- Check [Quick Reference](docs/QUICK_REFERENCE.md) for common tasks
- See [Troubleshooting](docs/DEPLOYMENT.md#troubleshooting)
- Review logs in `logs/` folder

## 📝 License

Proprietary - All rights reserved

## 🔄 Updates

The system includes auto-update capabilities:
```bash
python scripts/update_from_github.sh
```

See [Update System Documentation](docs/UPDATE_SYSTEM.md) for details.

---

**Version**: 2.0  
**Last Updated**: November 2025
