"""
TensorFlow Image Analysis Service — Phase 7 + Uncategorized Fix 2

Key addition in analyze_and_update_ticket:
  If text ML returned category=None AND image analysis finds a department,
  the ticket's category is set and route_ticket is called immediately
  so the ticket gets auto-assigned without waiting for a manager.
"""

import asyncio
import os
from typing import Optional

# ── Department keyword mapping ────────────────────────────
DEPT_KEYWORDS: dict[str, list[str]] = {
    "it": [
        "desktop_computer", "laptop", "notebook", "monitor", "screen",
        "keyboard", "computer_keyboard", "space_bar", "mouse",
        "hard_disc", "hard_disk", "hard_drive", "modem", "router",
        "hub", "printer", "scanner", "iPod", "USB_flash_drive",
        "web_site", "dial_telephone", "mobile_phone", "cellular_telephone",
        "television", "tv", "remote_control", "oscilloscope",
    ],
    "security": [
        "padlock", "combination_lock", "chain", "chainlink_fence",
        "barbed_wire", "prison", "jail",
        "revolver", "pistol", "rifle", "assault_rifle", "gun",
        "spotlight", "searchlight", "binoculars",
        "bulletproof_vest", "shield",
    ],
    "hr": [
        "stretcher", "ambulance", "wheelchair", "stethoscope",
        "syringe", "pill", "Band_Aid", "first_aid_kit", "lab_coat",
        "suit", "Windsor_tie", "necktie", "briefcase",
        "letter_opener", "envelope",
    ],
    "technical_operations": [
        "crane", "wrench", "screwdriver", "hammer", "mallet",
        "nail", "vise", "power_drill", "jackhammer", "chainsaw",
        "hand_saw", "electric_fan", "oil_filter", "radiator",
        "piston", "gear", "gearshift", "valve", "pump", "boiler",
        "generator", "fire_truck", "forklift", "tractor",
        "bulldozer", "excavator", "steam_engine", "lathe",
    ],
    "marketing": [
        "projector", "projection_screen", "loudspeaker", "speaker",
        "microphone", "banner", "signboard",
        "ballpoint", "fountain_pen", "binder", "clipboard",
        "envelope", "megaphone",
    ],
    "legal": [
        "book", "bookcase", "filing_cabinet",
        "notebook", "binder", "clipboard",
        "gavel", "balance_beam",
        "envelope", "mailbox",
    ],
}

DEPT_DESCRIPTIONS: dict[str, str] = {
    "it":                   "Information Technology",
    "security":             "Security",
    "hr":                   "Human Resources",
    "technical_operations": "Technical Operations",
    "marketing":            "Marketing",
    "legal":                "Legal",
}

_model = None


def _load_model():
    global _model
    if _model is None:
        try:
            import tensorflow as tf
            from tensorflow.keras.applications import MobileNetV2
            _model = MobileNetV2(weights="imagenet", include_top=True)
            print("🧠  TensorFlow MobileNetV2 model loaded")
        except ImportError:
            raise RuntimeError(
                "TensorFlow not installed. Run: pip install tensorflow-cpu --break-system-packages"
            )
    return _model


def _run_inference(image_path: str) -> dict:
    import numpy as np
    from tensorflow.keras.applications.mobilenet_v2 import preprocess_input, decode_predictions
    from tensorflow.keras.preprocessing import image as keras_image

    model = _load_model()

    try:
        img = keras_image.load_img(image_path, target_size=(224, 224))
    except Exception as e:
        return _error_result(f"Could not load image: {e}")

    img_array = keras_image.img_to_array(img)
    img_array = np.expand_dims(img_array, axis=0)
    img_array = preprocess_input(img_array)

    try:
        predictions = model.predict(img_array, verbose=0)
        decoded = decode_predictions(predictions, top=10)[0]
    except Exception as e:
        return _error_result(f"Inference failed: {e}")

    top_labels = [
        {"name": name.replace("_", " "), "confidence": round(float(conf), 4)}
        for (_, name, conf) in decoded
    ]

    dept_scores: dict[str, float] = {dept: 0.0 for dept in DEPT_KEYWORDS}
    for (_, label_name, conf) in decoded:
        label_lower = label_name.lower()
        for dept, keywords in DEPT_KEYWORDS.items():
            for kw in keywords:
                if kw.lower() in label_lower or label_lower in kw.lower():
                    dept_scores[dept] += float(conf)
                    break

    best_dept  = max(dept_scores, key=dept_scores.get)
    best_score = dept_scores[best_dept]
    dept_suggestion = best_dept if best_score >= 0.08 else None

    top_3_names = [label["name"] for label in top_labels[:3]]
    description_parts = [f"Image analysis detected: {', '.join(top_3_names)}."]

    if dept_suggestion:
        dept_label = DEPT_DESCRIPTIONS.get(dept_suggestion, dept_suggestion)
        description_parts.append(
            f"Content suggests this incident is related to the {dept_label} department "
            f"(image confidence: {best_score:.0%})."
        )
    else:
        description_parts.append(
            "Could not confidently map image content to a specific department. "
            "Manual review recommended."
        )

    if top_labels and top_labels[0]["confidence"] > 0.5:
        description_parts.append(
            f"Primary detection: {top_labels[0]['name']} "
            f"({top_labels[0]['confidence']:.0%} confidence)."
        )

    return {
        "success":            True,
        "department":         dept_suggestion,
        "confidence":         round(best_score, 4),
        "labels":             top_labels[:5],
        "description":        " ".join(description_parts),
        "raw_top_prediction": top_labels[0]["name"] if top_labels else None,
        "all_dept_scores":    {k: round(v, 4) for k, v in dept_scores.items()},
    }


def _error_result(message: str) -> dict:
    return {
        "success":            False,
        "department":         None,
        "confidence":         0.0,
        "labels":             [],
        "description":        f"Image analysis failed: {message}",
        "raw_top_prediction": None,
        "all_dept_scores":    {},
    }


async def analyze_image(image_path: str) -> dict:
    if not image_path:
        return _error_result("No image path provided")
    if not os.path.exists(image_path):
        return _error_result(f"Image file not found: {image_path}")

    ext = os.path.splitext(image_path)[1].lower()
    if ext not in {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}:
        return {
            "success":            False,
            "department":         None,
            "confidence":         0.0,
            "labels":             [],
            "description":        f"File type '{ext}' not supported for image analysis. Only JPEG/PNG are analyzed.",
            "raw_top_prediction": None,
            "all_dept_scores":    {},
        }

    try:
        result = await asyncio.to_thread(_run_inference, image_path)
        return result
    except RuntimeError as e:
        return _error_result(str(e))
    except Exception as e:
        return _error_result(f"Unexpected error: {e}")


async def analyze_and_update_ticket(
    ticket_id,
    image_path: str,
    db,
) -> Optional[dict]:
    """
    Run image analysis and persist results.

    ── Uncategorized Fix 2 ──
    If the text ML missed the category (ticket.category is None) and
    the image analysis finds one with enough confidence, we:
      1. Set ticket.category from the image result
      2. Re-run route_ticket so the ticket gets auto-assigned immediately
      3. Log the recovery in the audit trail
    This means an uncategorized ticket with a clear image can self-recover
    without any manager intervention.
    """
    from sqlalchemy import select
    from app.models.ticket import Ticket, TicketStatus
    from app.models.audit_log import AuditLog, AuditAction

    result = await analyze_image(image_path)

    try:
        ticket = await db.get(Ticket, ticket_id)
        if not ticket:
            return result

        # Always save the raw analysis results
        ticket.image_analysis_notes      = result["description"]
        ticket.image_analysis_department = result.get("department")
        ticket.image_analysis_confidence = result.get("confidence")

        image_dept       = result.get("department")
        image_confidence = result.get("confidence", 0)
        was_uncategorized = not ticket.category

        # ── Uncategorized Fix 2: recover via image ────────
        if (
            result["success"]
            and image_dept
            and was_uncategorized
            and image_confidence >= 0.15
        ):
            ticket.category     = image_dept
            ticket.ml_confidence = image_confidence

            # Re-trigger routing — ticket may now be auto-assigned
            if ticket.status == TicketStatus.OPEN and not ticket.assignee_id:
                from app.services.assignment_service import route_ticket
                routing = await route_ticket(ticket, db)
                routing_note = f"Image re-routing: {routing.get('reason','')}"
                print(
                    f"🔄  Uncategorized ticket {ticket_id} recovered via image → "
                    f"{image_dept} | {routing_note}"
                )
            else:
                routing_note = "Ticket already assigned — category updated only"

            # Log the recovery
            log = AuditLog(
                ticket_id=ticket.id,
                user_id=ticket.reporter_id,
                action=AuditAction.CATEGORY_SET,
                notes=(
                    f"Category recovered from image analysis: {image_dept} "
                    f"({image_confidence:.0%} confidence). {routing_note}"
                ),
            )
            db.add(log)

        else:
            # Standard audit log for image analysis
            labels_str = ", ".join(
                f"{l['name']} ({l['confidence']:.0%})"
                for l in result.get("labels", [])[:3]
            )
            log = AuditLog(
                ticket_id=ticket.id,
                user_id=ticket.reporter_id,
                action=AuditAction.IMAGE_ANALYZED,
                notes=(
                    f"Image analysis: {result['description']} "
                    f"Top labels: [{labels_str}]"
                ) if result["success"] else f"Image analysis failed: {result['description']}",
            )
            db.add(log)

        await db.flush()

    except Exception as e:
        print(f"⚠️  Image analysis DB update failed: {e}")

    return result
