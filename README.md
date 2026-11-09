# 📈 NiftyIQ: Nifty 100 Financial Intelligence Platform

**Live Demo:** [https://niftyiq.onrender.com](https://niftyiq.onrender.com)  
*(Note: As this is hosted on a free Render tier, the initial load may take 30-50 seconds if the server is waking from sleep. Subsequent clicks will be blazing fast!)*

---

## 🚀 Project Overview
NiftyIQ is a full-stack financial analytics and business intelligence platform engineered for the Nifty 100 universe. It provides structured fundamental data, automated financial health scores, multi-horizon visualizations, and deep valuation ratios. The platform is designed with a sleek, institutional-grade dark mode UI and features a robust, rate-limited REST API for external channel partners.

## ✨ Key Features
* **Advanced Data Visualizations:** Clean, interactive charts for multi-year revenue trends, balance sheet composition, cash flow waterfalls, and CAGR radars.
* **Automated Health Scoring:** Algorithmic multi-factor health scores evaluating the fundamental stability and growth of top Indian equities.
* **Partner API & Swagger UI:** Fully documented RESTful API with HMAC-SHA256 authentication and Redis-backed tiered rate limiting (Basic, Pro, Enterprise).
* **Robust Admin Dashboard:** Custom Django admin interface with real-time operational metrics, API usage telemetry, and data quality gap tracking.
* **Cloud-Native Deployment:** Fully containerized and deployed on Render with a serverless Neon PostgreSQL database.

## 🛠️ Tech Stack
* **Backend:** Python, Django, Django REST Framework (DRF)
* **Frontend:** Django Templates, HTML5, Tailwind CSS, JavaScript (Chart.js / ApexCharts)
* **Database:** PostgreSQL (Hosted on Neon)
* **Caching & Throttling:** Redis
* **Authentication:** Custom HMAC-SHA256 Request Signing
* **Deployment:** Render (Web Service), Gunicorn, WhiteNoise (Static Files)

## 📸 Platform Previews

*(Add screenshots of your project here by dragging and dropping them into GitHub)*

* **Dashboard & Screener:** `![Dashboard Placeholder]()`
* **Company Analytics:** `![Analytics Placeholder]()`
* **Admin Telemetry:** `![Admin Placeholder]()`
* **Swagger API Docs:** `![Swagger Placeholder]()`

## 💻 Local Development Setup

To run this project locally on your machine:

1. **Clone the repository:**
   ```bash
   git clone https://github.com/shreyashambade/nifty100-financial-intelligence-platform.git
   cd nifty100-financial-intelligence-platform
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows use: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up environment variables:**
   Create a `.env` file in the root directory and add:
   ```env
   SECRET_KEY=your_secret_key
   DEBUG=True
   CACHE_BACKEND=django.core.cache.backends.locmem.LocMemCache
   DATABASE_URL=your_neon_postgres_url
   ```

5. **Run migrations and start the server:**
   ```bash
   cd webapp
   python manage.py migrate
   python manage.py runserver
   ```
   *Access the app at `http://127.0.0.1:8000/`*

---
*Designed and built by [Your Name/Shreyash Ambade]*