from __future__ import annotations

import json
import os
import re
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import URLError
from uuid import uuid4

from flask import Flask, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.utils import secure_filename

from feaston.booking import BookingRequest, BookingDecision, evaluate_booking
from feaston.excel_store import append_row, read_rows, write_rows
from feaston.shop import Product, build_cart_items, delivery_charge, find_product, load_products, save_products, subtotal_from_cart_items

BASE_DIR = Path(__file__).resolve().parent
DOCUMENTS_DIR = Path(os.getenv("FEASTON_DOCUMENTS_DIR") or (BASE_DIR / "documents"))
PRODUCT_STORE = DOCUMENTS_DIR / "products.xlsx"
ROOM_PHOTO_STORE = DOCUMENTS_DIR / "room_photos.xlsx"
BOOKING_STORE = DOCUMENTS_DIR / "bookings.xlsx"
SHOP_ORDER_STORE = DOCUMENTS_DIR / "shop_orders.xlsx"
IMAGE_DIR = DOCUMENTS_DIR / "images"
ROOM_UPLOAD_DIR = IMAGE_DIR / "rooms"
PRODUCT_UPLOAD_DIR = IMAGE_DIR / "products"
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
READ_ONLY = os.getenv("FEASTON_READ_ONLY", "1" if os.getenv("VERCEL") else "0") == "1"
READ_ONLY_MESSAGE = (
    "This live site is read-only. Add photos/products by updating the Excel files and images "
    "in the documents/ folder locally, then commit and redeploy."
)
ADMIN_TABS = {
    "bookings": "Stay Bookings",
    "orders": "Shop Orders",
    "photos": "Room Photos",
    "products": "Products",
}
EXPORTABLE_STORES = {
    "bookings": BOOKING_STORE,
    "orders": SHOP_ORDER_STORE,
    "products": PRODUCT_STORE,
    "photos": ROOM_PHOTO_STORE,
}
ROOM_PHOTO_COLUMNS = ["id", "caption", "image_path", "uploaded_at_utc"]
BOOKING_COLUMNS = [
    "submitted_at_utc", "days", "people", "group_type", "adults", "children", "infants",
    "resident_status", "primary_name", "second_name", "notification_method", "contact_email",
    "contact_mobile", "primary_aadhaar", "second_aadhaar", "primary_passport", "second_passport",
    "visa_details", "return_travel_details", "notification_status", "notification_detail",
]
SHOP_ORDER_COLUMNS = [
    "submitted_at_utc", "customer_name", "customer_email", "customer_mobile", "fulfillment_type",
    "distance_km", "items", "subtotal", "delivery_charge", "total", "delivery_eta_text", "notifications",
]


def _parse_int(value: str | None, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _parse_float(value: str | None, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _clean(value: str | None) -> str:
    return value.strip() if value else ""


def _ensure_runtime_dirs() -> None:
    if READ_ONLY:
        return
    ROOM_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    PRODUCT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if not PRODUCT_STORE.exists():
        save_products(PRODUCT_STORE, load_products(PRODUCT_STORE))
    if not ROOM_PHOTO_STORE.exists():
        write_rows(ROOM_PHOTO_STORE, ROOM_PHOTO_COLUMNS, [], "RoomPhotos")


def _load_products() -> list[Product]:
    return load_products(PRODUCT_STORE)


def _save_products(products: list[Product]) -> None:
    save_products(PRODUCT_STORE, products)


def _load_room_photos() -> list[dict]:
    return [row for row in read_rows(ROOM_PHOTO_STORE) if row.get("image_path")]


def _save_room_photos(photos: list[dict]) -> None:
    write_rows(ROOM_PHOTO_STORE, ROOM_PHOTO_COLUMNS, photos, "RoomPhotos")


def _is_allowed_image(filename: str) -> bool:
    suffix = Path(filename).suffix.lower().replace(".", "")
    return suffix in ALLOWED_IMAGE_EXTENSIONS


def _save_uploaded_image(uploaded_file, destination_dir: Path, prefix: str) -> str:
    if uploaded_file is None or not uploaded_file.filename:
        raise ValueError("Please choose an image file.")

    raw_name = secure_filename(uploaded_file.filename)
    if not raw_name or not _is_allowed_image(raw_name):
        raise ValueError("Only PNG, JPG, JPEG, or WEBP images are supported.")

    extension = Path(raw_name).suffix.lower()
    filename = f"{prefix}-{uuid4().hex[:10]}{extension}"
    output_path = destination_dir / filename
    uploaded_file.save(output_path)
    return f"{destination_dir.name}/{filename}"


def _slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return cleaned or f"product-{uuid4().hex[:6]}"


def _save_submission(payload: dict) -> None:
    if READ_ONLY:
        return
    notification = payload.get("notification", {})
    row = {
        "submitted_at_utc": payload["submitted_at_utc"],
        **payload["booking"],
        **payload["documents"],
        "notification_status": notification.get("status", ""),
        "notification_detail": notification.get("detail", ""),
    }
    append_row(BOOKING_STORE, BOOKING_COLUMNS, row, "Bookings")


def _save_shop_order(payload: dict) -> None:
    if READ_ONLY:
        return
    row = {**payload, "items": "; ".join(f"{i['name']} x{i['quantity']}" for i in payload["cart_items"])}
    append_row(SHOP_ORDER_STORE, SHOP_ORDER_COLUMNS, row, "Orders")


def _send_email_alert(subject: str, body: str, destination: str) -> dict:
    smtp_host = os.getenv("SMTP_HOST")
    if not smtp_host:
        return {"channel": "email", "status": "not_configured", "detail": "SMTP_HOST is not configured."}
    if not destination:
        return {"channel": "email", "status": "failed", "detail": "Destination email address is missing."}

    smtp_port = _parse_int(os.getenv("SMTP_PORT"), 587)
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    smtp_from = os.getenv("SMTP_FROM", smtp_user or "noreply@feaston.local")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = smtp_from
    message["To"] = destination
    message.set_content(body)

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as smtp:
            smtp.starttls()
            if smtp_user and smtp_password:
                smtp.login(smtp_user, smtp_password)
            smtp.send_message(message)
    except Exception as exc:  # pragma: no cover - network-dependent
        return {"channel": "email", "status": "failed", "detail": str(exc)}

    return {"channel": "email", "status": "sent", "detail": f"Email sent to {destination}."}


def _send_mobile_alert(message: str) -> dict:
    webhook_url = os.getenv("BOOKING_SMS_WEBHOOK_URL")
    if not webhook_url:
        return {
            "channel": "mobile",
            "status": "not_configured",
            "detail": "BOOKING_SMS_WEBHOOK_URL is not configured.",
        }

    payload = json.dumps({"text": message}).encode("utf-8")
    req = urlrequest.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=15) as response:  # noqa: S310
            status_code = response.status
    except URLError as exc:  # pragma: no cover - network-dependent
        return {"channel": "mobile", "status": "failed", "detail": str(exc)}

    return {
        "channel": "mobile",
        "status": "sent",
        "detail": f"Mobile webhook accepted request (HTTP {status_code}).",
    }


def _dispatch_notification(doc_data: dict, booking_data: dict) -> dict:
    message = (
        "New FeastOn booking request: "
        f"{booking_data['days']} day(s), {booking_data['people']} guest(s), "
        f"group={booking_data['group_type']}, primary={doc_data['primary_name']}."
    )
    if doc_data["notification_method"] == "email":
        return _send_email_alert(
            subject="FeastOn booking document submission",
            body=message,
            destination=doc_data["contact_email"],
        )
    return _send_mobile_alert(message)


def _document_defaults() -> dict:
    return {
        "notification_method": "email",
        "contact_email": "",
        "contact_mobile": "",
        "resident_status": "india",
        "primary_name": "",
        "second_name": "",
        "primary_aadhaar": "",
        "second_aadhaar": "",
        "primary_passport": "",
        "second_passport": "",
        "visa_details": "",
        "return_travel_details": "",
    }


def _validate_documents(data: dict, people_count: int) -> list[str]:
    errors: list[str] = []

    if not data["primary_name"]:
        errors.append("Primary guest name is required.")
    if people_count == 2 and not data["second_name"]:
        errors.append("Second guest name is required for 2-person bookings.")

    if data["notification_method"] == "email" and not data["contact_email"]:
        errors.append("Email is required when notification method is email.")
    if data["notification_method"] == "mobile" and not data["contact_mobile"]:
        errors.append("Mobile number is required when notification method is mobile.")

    if data["resident_status"] == "india":
        if not data["primary_aadhaar"]:
            errors.append("One person's Aadhaar is mandatory for Indian guests.")
        if people_count == 2 and not data["second_aadhaar"]:
            errors.append("Aadhaar is required for the second guest during check-in verification.")
    elif data["resident_status"] == "outside_india":
        if not data["primary_passport"]:
            errors.append("Passport number is mandatory for international guests.")
        if people_count == 2 and not data["second_passport"]:
            errors.append("Second guest passport number is mandatory.")
        if not data["visa_details"]:
            errors.append("Visiting visa details are mandatory for international guests.")
        if not data["return_travel_details"]:
            errors.append("Return flight or onward travel details are mandatory.")
    else:
        errors.append("Please select valid residency details.")

    return errors


def _shop_checkout_defaults() -> dict:
    return {
        "customer_name": "",
        "customer_email": "",
        "customer_mobile": "",
        "fulfillment_type": "pickup",
        "distance_km": "0",
    }


def _cart_from_session(products: list[Product]) -> dict[str, int]:
    raw = session.get("shop_cart", {})
    if not isinstance(raw, dict):
        return {}
    sanitized: dict[str, int] = {}
    for product_id, qty in raw.items():
        if find_product(product_id, products) is None:
            continue
        if not isinstance(qty, int):
            qty = _parse_int(str(qty), 0)
        if qty > 0:
            sanitized[product_id] = qty
    return sanitized


def _cart_item_count(cart_payload: dict[str, int]) -> int:
    return sum(cart_payload.values())


def _compose_shop_order_message(order_data: dict) -> str:
    lines = [
        "FeastOn Shop Order Details",
        f"Customer: {order_data['customer_name']}",
        f"Email: {order_data['customer_email']}",
        f"Mobile: {order_data['customer_mobile'] or 'Not provided'}",
        f"Fulfillment: {order_data['fulfillment_type']}",
        f"Distance (km): {order_data['distance_km']}",
        "Items:",
    ]
    for item in order_data["cart_items"]:
        lines.append(
            f"- {item['name']} x{item['quantity']} @ INR {item['unit_price']} = INR {item['line_total']}"
        )
    lines.extend(
        [
            f"Subtotal: INR {order_data['subtotal']}",
            f"Delivery Charge: INR {order_data['delivery_charge']}",
            f"Total: INR {order_data['total']}",
            f"Expected Delivery: {order_data['delivery_eta_text']}",
        ]
    )
    return "\n".join(lines)


def _is_admin_authenticated() -> bool:
    return bool(session.get("is_admin"))


def _dispatch_shop_notifications(order_data: dict) -> dict:
    purchaser_result = _send_email_alert(
        subject="FeastOn shop order confirmation",
        body=_compose_shop_order_message(order_data),
        destination=order_data["customer_email"],
    )

    admin_email = os.getenv("SHOP_ADMIN_EMAIL") or os.getenv("ADMIN_EMAIL", "")
    if admin_email:
        admin_result = _send_email_alert(
            subject="FeastOn shop order received",
            body=_compose_shop_order_message(order_data),
            destination=admin_email,
        )
    else:
        admin_result = {
            "channel": "email",
            "status": "not_configured",
            "detail": "SHOP_ADMIN_EMAIL is not configured.",
        }

    mobile_result = {"channel": "mobile", "status": "skipped", "detail": "Mobile alert not requested."}
    if order_data["customer_mobile"]:
        mobile_result = _send_mobile_alert(
            (
                "Shop order for "
                f"{order_data['customer_name']} ({order_data['customer_mobile']}) - "
                f"Total INR {order_data['total']}, ETA {order_data['delivery_eta_text']}."
            )
        )

    return {
        "purchaser": purchaser_result,
        "admin": admin_result,
        "mobile": mobile_result,
    }


def create_app() -> Flask:
    _ensure_runtime_dirs()
    _load_products()

    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev-only-secret-change-me")

    @app.context_processor
    def inject_storage_mode():
        return {"read_only": READ_ONLY, "read_only_message": READ_ONLY_MESSAGE}

    @app.route("/media/<path:filename>")
    def media(filename: str):
        return send_from_directory(IMAGE_DIR, filename)

    def _reject_if_read_only():
        if READ_ONLY:
            session["admin_errors"] = [READ_ONLY_MESSAGE]
            return redirect(url_for("admin_dashboard"))
        return None

    @app.route("/", methods=["GET", "POST"])
    def index():
        form_data = {
            "days": 1,
            "group_type": "family",
            "people": 1,
            "adults": 1,
            "children": 0,
            "infants": 0,
        }
        decision = BookingDecision(is_valid=False)

        if request.method == "POST":
            form_data = {
                "days": _parse_int(request.form.get("days"), 1),
                "group_type": request.form.get("group_type", "family"),
                "people": _parse_int(request.form.get("people"), 1),
                "adults": _parse_int(request.form.get("adults"), 1),
                "children": _parse_int(request.form.get("children"), 0),
                "infants": _parse_int(request.form.get("infants"), 0),
            }

            booking_request = BookingRequest(
                days=form_data["days"],
                people=form_data["people"],
                adults=form_data["adults"],
                children=form_data["children"],
                infants=form_data["infants"],
                group_type=form_data["group_type"],
            )
            decision = evaluate_booking(booking_request)
            if decision.is_valid:
                session["booking"] = form_data
                session["booking_decision"] = {
                    "recommended_rooms": decision.recommended_rooms,
                    "nightly_rate": decision.nightly_rate,
                    "estimated_total": decision.estimated_total,
                    "long_stay_discount": decision.long_stay_discount,
                }
                return redirect(url_for("guest_documents"))

        return render_template(
            "index.html",
            form_data=form_data,
            decision=decision,
            room_photos=_load_room_photos(),
        )

    def _render_shop_page(
        *,
        checkout_data: dict | None = None,
        errors: list[str] | None = None,
    ):
        products = _load_products()
        cart_payload = _cart_from_session(products)
        cart_items = build_cart_items(cart_payload, products)
        subtotal = subtotal_from_cart_items(cart_items)
        checkout_data = checkout_data or _shop_checkout_defaults()

        fulfillment_type = checkout_data.get("fulfillment_type", "pickup")
        distance_km = _parse_float(str(checkout_data.get("distance_km", "0")), 0.0)
        charge = delivery_charge(distance_km, fulfillment_type == "delivery")

        return render_template(
            "shop.html",
            products=products,
            cart_items=cart_items,
            cart_count=_cart_item_count(cart_payload),
            subtotal=subtotal,
            checkout_data=checkout_data,
            delivery_charge_preview=charge,
            total_preview=subtotal + charge,
            errors=errors or [],
            notice=session.pop("shop_notice", ""),
            order_result=session.pop("shop_order_result", None),
        )

    @app.route("/shop", methods=["GET"])
    def shop_page():
        return _render_shop_page()

    @app.route("/shop/add", methods=["POST"])
    def add_to_cart():
        products = _load_products()
        product_id = _clean(request.form.get("product_id"))
        quantity = _parse_int(request.form.get("quantity"), 1)

        product = find_product(product_id, products)
        if product is None:
            session["shop_notice"] = "Unable to add item: invalid product."
            return redirect(url_for("shop_page"))

        quantity = max(1, min(quantity, 25))
        cart_payload = _cart_from_session(products)
        cart_payload[product_id] = cart_payload.get(product_id, 0) + quantity
        session["shop_cart"] = cart_payload
        session["shop_notice"] = f"Added {quantity} x {product.name} to cart."
        return redirect(url_for("shop_page"))

    @app.route("/shop/clear", methods=["POST"])
    def clear_cart():
        session["shop_cart"] = {}
        session["shop_notice"] = "Cart cleared."
        return redirect(url_for("shop_page"))

    @app.route("/shop/checkout", methods=["POST"])
    def checkout_cart():
        products = _load_products()
        cart_payload = _cart_from_session(products)
        cart_items = build_cart_items(cart_payload, products)

        checkout_data = {
            "customer_name": _clean(request.form.get("customer_name")),
            "customer_email": _clean(request.form.get("customer_email")),
            "customer_mobile": _clean(request.form.get("customer_mobile")),
            "fulfillment_type": _clean(request.form.get("fulfillment_type")) or "pickup",
            "distance_km": _clean(request.form.get("distance_km")) or "0",
        }

        errors: list[str] = []
        if not cart_items:
            errors.append("Please add at least one product to continue checkout.")
        if not checkout_data["customer_name"]:
            errors.append("Purchaser name is required.")
        if not checkout_data["customer_email"]:
            errors.append("Purchaser email is required to share order details.")

        if checkout_data["fulfillment_type"] not in {"pickup", "delivery"}:
            errors.append("Please select a valid fulfillment type.")

        distance_km = _parse_float(checkout_data["distance_km"], 0.0)
        if checkout_data["fulfillment_type"] == "delivery" and distance_km < 0:
            errors.append("Delivery distance cannot be negative.")

        if errors:
            return _render_shop_page(checkout_data=checkout_data, errors=errors)

        subtotal = subtotal_from_cart_items(cart_items)
        charge = delivery_charge(distance_km, checkout_data["fulfillment_type"] == "delivery")
        total = subtotal + charge
        eta_date = datetime.now().date() + timedelta(days=11)
        eta_text = f"{eta_date.strftime('%d %b %Y')} (about 1.5 weeks)"

        order_data = {
            "submitted_at_utc": datetime.now(timezone.utc).isoformat(),
            "customer_name": checkout_data["customer_name"],
            "customer_email": checkout_data["customer_email"],
            "customer_mobile": checkout_data["customer_mobile"],
            "fulfillment_type": checkout_data["fulfillment_type"],
            "distance_km": distance_km,
            "cart_items": cart_items,
            "subtotal": subtotal,
            "delivery_charge": charge,
            "total": total,
            "delivery_eta_text": eta_text,
        }

        notification_results = _dispatch_shop_notifications(order_data)
        order_data["notifications"] = notification_results
        _save_shop_order(order_data)

        session["shop_cart"] = {}
        session["shop_notice"] = "Order placed. Details shared with purchaser and admin."
        session["shop_order_result"] = {
            "total": total,
            "delivery_charge": charge,
            "delivery_eta_text": eta_text,
            "notifications": notification_results,
        }
        return redirect(url_for("shop_page"))

    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        if _is_admin_authenticated():
            return redirect(url_for("admin_dashboard"))

        error = ""
        if request.method == "POST":
            username = _clean(request.form.get("username"))
            password = _clean(request.form.get("password"))
            expected_username = os.getenv("ADMIN_USERNAME", "admin")
            expected_password = os.getenv("ADMIN_PASSWORD", "admin123")

            if username == expected_username and password == expected_password:
                session["is_admin"] = True
                session["admin_user"] = username
                return redirect(url_for("admin_dashboard"))
            error = "Invalid admin credentials."

        return render_template("admin_login.html", error=error)

    @app.route("/admin/logout", methods=["POST"])
    def admin_logout():
        session.pop("is_admin", None)
        session.pop("admin_user", None)
        return redirect(url_for("admin_login"))

    @app.route("/admin", methods=["GET"])
    def admin_dashboard():
        if not _is_admin_authenticated():
            return redirect(url_for("admin_login"))

        active_tab = request.args.get("tab", "bookings")
        if active_tab not in ADMIN_TABS:
            active_tab = "bookings"

        return render_template(
            "admin_dashboard.html",
            admin_user=session.get("admin_user", "admin"),
            active_tab=active_tab,
            tabs=ADMIN_TABS,
            bookings=list(reversed(read_rows(BOOKING_STORE))),
            shop_orders=list(reversed(read_rows(SHOP_ORDER_STORE))),
            products=_load_products(),
            room_photos=_load_room_photos(),
            notice=session.pop("admin_notice", ""),
            errors=session.pop("admin_errors", []),
        )

    @app.route("/admin/export/<name>", methods=["GET"])
    def admin_export(name: str):
        if not _is_admin_authenticated():
            return redirect(url_for("admin_login"))
        store = EXPORTABLE_STORES.get(name)
        if store is None or not store.exists():
            session["admin_errors"] = [f"No {name} Excel file exists yet."]
            return redirect(url_for("admin_dashboard"))
        return send_from_directory(store.parent, store.name, as_attachment=True)

    @app.route("/admin/room-photo", methods=["POST"])
    def admin_add_room_photo():
        if not _is_admin_authenticated():
            return redirect(url_for("admin_login"))
        if (blocked := _reject_if_read_only()) is not None:
            return blocked

        caption = _clean(request.form.get("caption"))
        photo = request.files.get("photo")
        try:
            image_path = _save_uploaded_image(photo, ROOM_UPLOAD_DIR, "room")
        except ValueError as exc:
            session["admin_errors"] = [str(exc)]
            return redirect(url_for("admin_dashboard", tab="photos"))

        room_photos = _load_room_photos()
        room_photos.insert(
            0,
            {
                "id": f"room-{uuid4().hex[:8]}",
                "caption": caption or "FeastOn Room",
                "image_path": image_path,
                "uploaded_at_utc": datetime.now(timezone.utc).isoformat(),
            },
        )
        _save_room_photos(room_photos)
        session["admin_notice"] = "Room photo added successfully."
        return redirect(url_for("admin_dashboard", tab="photos"))

    @app.route("/admin/product/add", methods=["POST"])
    def admin_add_product():
        if not _is_admin_authenticated():
            return redirect(url_for("admin_login"))
        if (blocked := _reject_if_read_only()) is not None:
            return blocked

        name = _clean(request.form.get("name"))
        category = _clean(request.form.get("category")) or "Spices"
        unit_label = _clean(request.form.get("unit_label")) or "1 unit"
        description = _clean(request.form.get("description"))
        unit_price = _parse_int(request.form.get("unit_price"), 0)

        errors: list[str] = []
        if not name:
            errors.append("Product name is required.")
        if unit_price <= 0:
            errors.append("Product price must be greater than zero.")
        if category not in {"Spices", "Handicrafts"}:
            errors.append("Category should be Spices or Handicrafts.")

        image_path = ""
        image_file = request.files.get("photo")
        if image_file and image_file.filename:
            try:
                image_path = _save_uploaded_image(image_file, PRODUCT_UPLOAD_DIR, "product")
            except ValueError as exc:
                errors.append(str(exc))

        if errors:
            session["admin_errors"] = errors
            return redirect(url_for("admin_dashboard", tab="products"))

        product_id = f"{_slugify(name)}-{uuid4().hex[:6]}"
        products = _load_products()
        products.append(
            Product(
                id=product_id,
                name=name,
                category=category,
                unit_price=unit_price,
                unit_label=unit_label,
                description=description,
                image_path=image_path,
            )
        )
        _save_products(products)
        session["admin_notice"] = f"Product '{name}' added."
        return redirect(url_for("admin_dashboard", tab="products"))

    @app.route("/admin/product/<product_id>/photo", methods=["POST"])
    def admin_update_product_photo(product_id: str):
        if not _is_admin_authenticated():
            return redirect(url_for("admin_login"))
        if (blocked := _reject_if_read_only()) is not None:
            return blocked

        photo = request.files.get("photo")
        try:
            image_path = _save_uploaded_image(photo, PRODUCT_UPLOAD_DIR, "product")
        except ValueError as exc:
            session["admin_errors"] = [str(exc)]
            return redirect(url_for("admin_dashboard", tab="products"))

        products = _load_products()
        updated = False
        for product in products:
            if product.id == product_id:
                product.image_path = image_path
                updated = True
                break

        if not updated:
            session["admin_errors"] = ["Product not found for image upload."]
            return redirect(url_for("admin_dashboard", tab="products"))

        _save_products(products)
        session["admin_notice"] = "Product image updated."
        return redirect(url_for("admin_dashboard", tab="products"))

    @app.route("/guest-documents", methods=["GET", "POST"])
    def guest_documents():
        booking_data = session.get("booking")
        if not booking_data:
            return redirect(url_for("index"))

        people_count = _parse_int(str(booking_data.get("people", 1)), 1)
        form_data = _document_defaults()
        errors: list[str] = []

        if request.method == "POST":
            form_data = {
                "notification_method": _clean(request.form.get("notification_method")) or "email",
                "contact_email": _clean(request.form.get("contact_email")),
                "contact_mobile": _clean(request.form.get("contact_mobile")),
                "resident_status": _clean(request.form.get("resident_status")) or "india",
                "primary_name": _clean(request.form.get("primary_name")),
                "second_name": _clean(request.form.get("second_name")),
                "primary_aadhaar": _clean(request.form.get("primary_aadhaar")),
                "second_aadhaar": _clean(request.form.get("second_aadhaar")),
                "primary_passport": _clean(request.form.get("primary_passport")),
                "second_passport": _clean(request.form.get("second_passport")),
                "visa_details": _clean(request.form.get("visa_details")),
                "return_travel_details": _clean(request.form.get("return_travel_details")),
            }
            errors = _validate_documents(form_data, people_count)
            if not errors:
                notification_result = _dispatch_notification(form_data, booking_data)
                record = {
                    "submitted_at_utc": datetime.now(timezone.utc).isoformat(),
                    "booking": booking_data,
                    "documents": form_data,
                    "notification": notification_result,
                    "checkin_requirement": (
                        "All guests must present original hard-copy documents during check-in verification."
                    ),
                }
                _save_submission(record)
                session["confirmation"] = {
                    "booking": booking_data,
                    "documents": {
                        "resident_status": form_data["resident_status"],
                        "primary_name": form_data["primary_name"],
                        "second_name": form_data["second_name"],
                    },
                    "notification": notification_result,
                }
                return redirect(url_for("confirmation"))

        return render_template(
            "documents.html",
            booking_data=booking_data,
            booking_decision=session.get("booking_decision", {}),
            form_data=form_data,
            errors=errors,
        )

    @app.route("/confirmation")
    def confirmation():
        confirmation_data = session.get("confirmation")
        if not confirmation_data:
            return redirect(url_for("index"))
        return render_template("confirmation.html", confirmation=confirmation_data)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
