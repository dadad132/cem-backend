# CRM Deployment Guide - Access from Anywhere

## ✅ New CRM Features Added

### 1. **Companies** 
- Track organizations/businesses
- Industry, website, phone, email, address
- Notes and custom fields

### 2. **Contacts**
- Individual people with full contact info
- Link to companies
- Job titles, LinkedIn, Twitter
- Phone, mobile, email

### 3. **Leads**
- Sales leads with status tracking (New, Contacted, Qualified, Converted)
- Lead source tracking (Website, Referral, Social Media, etc.)
- Estimated value
- Assign to sales reps
- Convert to contacts when qualified

### 4. **Deals (Sales Opportunities)**
- Sales pipeline with stages (Prospecting → Qualification → Proposal → Negotiation → Won/Lost)
- Deal value and probability
- Expected close dates
- Link to contacts and companies
- Assign to sales reps

### 5. **Activity Log**
- Track all interactions (Calls, Emails, Meetings, Notes)
- Link activities to leads, contacts, companies, or deals
- Duration tracking
- Outcome tracking

---

## 🌐 Deploy to Access from Anywhere

### Option 1: **Render.com (Recommended - FREE)**

1. **Push Code to GitHub:**
```powershell
cd C:\Users\admin\Documents\JP\Python\crm-backend
git init
git add .
git commit -m "CRM system with project management"
git remote add origin https://github.com/YOUR_USERNAME/crm-backend.git
git push -u origin main
```

2. **Create render.yaml:**
```yaml
services:
  - type: web
    name: crm-backend
    runtime: python
    buildCommand: "pip install -r requirements.txt"
    startCommand: "uvicorn app.main:app --host 0.0.0.0 --port $PORT"
    envVars:
      - key: DATABASE_URL
        value: sqlite+aiosqlite:///./data.db
      - key: SECRET_KEY
        generateValue: true
      - key: DEBUG
        value: false
```

3. **Sign up at [render.com](https://render.com)**
4. **Connect GitHub repo**
5. **Deploy** - Render will give you a URL like: `https://your-crm.onrender.com`

**Pros:** Free tier, automatic HTTPS, easy setup
**Cons:** Spins down after inactivity (30 sec startup delay)

---

### Option 2: **Railway.app (FREE)**

1. **Install Railway CLI:**
```powershell
npm install -g @railway/cli
```

2. **Deploy:**
```powershell
cd C:\Users\admin\Documents\JP\Python\crm-backend
railway login
railway init
railway up
```

3. **Get your URL:**
```powershell
railway domain
```

**Pros:** Fast, stays active, generous free tier
**Cons:** Requires credit card (won't charge)

---

### Option 3: **Fly.io (FREE - Best Performance)**

1. **Install flyctl:**
```powershell
powershell -Command "iwr https://fly.io/install.ps1 -useb | iex"
```

2. **Create fly.toml:**
```toml
app = "your-crm-app"
primary_region = "iad"

[build]
  builder = "paketobuildpacks/builder:base"

[env]
  PORT = "8000"

[[services]]
  internal_port = 8000
  protocol = "tcp"

  [[services.ports]]
    handlers = ["http"]
    port = 80

  [[services.ports]]
    handlers = ["tls", "http"]
    port = 443

[http_service]
  internal_port = 8000
  force_https = true
```

3. **Deploy:**
```powershell
cd C:\Users\admin\Documents\JP\Python\crm-backend
fly auth login
fly launch
fly deploy
```

**Pros:** Best performance, stays active, 3 free apps
**Cons:** Slightly more complex setup

---

### Option 4: **ngrok (Quick Test - Temporary URL)**

1. **Install ngrok:**
```powershell
choco install ngrok
```

2. **Run your server:**
```powershell
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

3. **In another terminal:**
```powershell
ngrok http 8000
```

4. **Get URL:** `https://abc123.ngrok.io`

**Pros:** Instant, no deployment needed
**Cons:** URL changes each time, requires computer running

---

### Option 5: **PythonAnywhere (FREE)**

1. **Sign up at [pythonanywhere.com](https://www.pythonanywhere.com)**
2. **Upload code** via web interface or git
3. **Configure WSGI:**
```python
import sys
path = '/home/username/crm-backend'
if path not in sys.path:
    sys.path.append(path)

from app.main import app as application
```

4. **Access:** `https://username.pythonanywhere.com`

**Pros:** Simple, persistent, free
**Cons:** Limited resources, slower

---

## 🔒 Production Security Checklist

1. **Environment Variables:**
```python
# Add to .env
DATABASE_URL=postgresql://...  # Use PostgreSQL in production
SECRET_KEY=your-secret-key-here
DEBUG=false
ALLOWED_HOSTS=your-domain.com
```

2. **Use PostgreSQL instead of SQLite:**
```python
# In app/core/config.py
database_url: str = "postgresql+asyncpg://user:pass@host/dbname"
```

3. **Enable HTTPS** (automatic with Render, Railway, Fly)

4. **Set Strong SECRET_KEY:**
```python
import secrets
print(secrets.token_urlsafe(32))
```

5. **Configure CORS properly:**
```python
# In app/main.py
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://yourdomain.com"],  # Specific domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## 📱 Mobile Access

Once deployed, your CRM works on:
- ✅ Any browser (Chrome, Safari, Firefox)
- ✅ Mobile phones (iOS, Android)
- ✅ Tablets
- ✅ Any computer with internet

Just visit your deployed URL!

---

## 🚀 Recommended: Railway (Easiest)

```powershell
# One-time setup
npm install -g @railway/cli
cd C:\Users\admin\Documents\JP\Python\crm-backend

# Deploy
railway login
railway init
railway up

# Get URL
railway domain
```

**Done!** Access from anywhere: `https://your-app.up.railway.app`

---

## 💡 Next Steps

1. Deploy to Railway or Render
2. Share the URL with your team
3. Everyone can access from their devices
4. Data is centralized and synchronized
5. Add custom domain (optional): `crm.yourcompany.com`

---

## 🆘 Need Help?

- **Railway Docs:** https://docs.railway.app
- **Render Docs:** https://render.com/docs
- **Fly.io Docs:** https://fly.io/docs
