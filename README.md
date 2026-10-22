# FeastOn Homestay Booking UI

A Python + Flask project for a family-friendly homestay in Tripunithura, Kochi (near Chottanikkara Devi Temple and Hill Palace Museum).

## Features

- Stay validation with duration range **1 to 60 days**.
- Two-step booking flow: stay details -> guest document verification.
- Guest planning by total people, group type, adults, children, and infants.
- Current booking slot supports **1 or 2 guests**.
- Separate **shop page** for spices and handcrafted items with add-to-cart.
- Direct purchase/pickup or delivery option with free delivery up to 5 km.
- Beyond 5 km, delivery fee is **INR 10 per extra kilometer**.
- Cart checkout shares order details to purchaser and admin, with 1.5-week delivery expectation.
- Admin login and dashboard to upload room photos.
- Admin can add new products and upload/update product photos.
- Mobile-friendly, themed UI tailored for a Kerala heritage homestay.
- Submission log with notification dispatch through email/mobile webhook.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open: `http://127.0.0.1:5000`

## Data storage (Excel, no database)

All data lives in the `documents/` folder:

- `documents/products.xlsx` - shop catalog (edit in Excel to add/change products)
- `documents/room_photos.xlsx` - room gallery (`image_path` is relative to `documents/images/`)
- `documents/images/rooms/`, `documents/images/products/` - image files
- `documents/bookings.xlsx`, `documents/shop_orders.xlsx` - created locally on first booking/order (git-ignored, contain guest ID data)

On Vercel (`VERCEL` env set) the app runs **read-only**: it reads the committed Excel files and images,
but admin uploads are disabled and bookings/orders are not saved (email/SMS notifications still go out).
To update the live site, run the app locally, use the admin dashboard (or edit the Excel files directly),
then commit `documents/` and redeploy. Override with `FEASTON_READ_ONLY=0|1` or `FEASTON_DOCUMENTS_DIR`.

## Optional notification setup

Email notifications require SMTP environment variables:

```bash
export SMTP_HOST="smtp.example.com"
export SMTP_PORT="587"
export SMTP_USER="username"
export SMTP_PASSWORD="password"
export SMTP_FROM="bookings@yourdomain.com"
export SHOP_ADMIN_EMAIL="admin@yourdomain.com"
export ADMIN_USERNAME="admin"
export ADMIN_PASSWORD="change-this-password"
```

Mobile notifications can be routed to any SMS gateway webhook:

```bash
export BOOKING_SMS_WEBHOOK_URL="https://your-sms-gateway-webhook"
```

## Run tests

```bash
pytest
```
