"""محلل رسائل دخول القناة.

يصنّف رسالة القناة إلى:
  entry   : دخول/صفقة جديدة (بيع أو شراء برقم ومهلة/ستوب)
  stop    : إشارة ضرب الستوب/خسارة لدخول قائم
  target  : إشارة تأمين/هدف/نقاط لدخول قائم (تتجمّع على نفس الدخول)
  note    : نص عام يُعتبر تحديثاً تابعاً للدخول الأحدث
  ignore  : رسائل إدارية/غير ذات صلة

التجميع: نفس صورة الدخول (image hash) تطابق أحدث دخول نشط، وإلا نصّي
يُسند لأحدث دخول نشط خلال نافذة زمنية. صورة مختلفة تماماً = دخول جديد.
"""
import re

# ── اتجاهات ──────────────────────────────────────────────
SELL_TOKENS = ("بيع", "بايع", "سيل", "sell", "short", "شورت", "على البيع", "البيع")
BUY_TOKENS = ("شراء", "شاري", "buy", "باي", "شرا", "long", "لونق", "الشراء")

STOP_TOKENS = ("استوب", "ستوب", "stop", "ضرب الستوب", "ضرب_الستوب", "ضرب", "خسارة")
TARGET_TOKENS = ("تأمين", "هدف", "اهداف", "target", "نقطه", "نقطة", "رصيد", "لاين")

ENTRY_TOKENS = (
    "دخول", "صفقة", "صفقه", "بيع", "شراء", "كسر", "اختراق", "شمعة",
    "انتظار", "عقد", "تنفيذ", "سهم", "عملة", "زوج", "حركة",
)

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def normalize(text: str) -> str:
    t = (text or "").strip()
    t = t.translate(_ARABIC_DIGITS)
    t = t.replace("ي", "ي").replace("ى", "ي").replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    t = t.replace("ة", "ه").replace("‏", "").replace("\u200e", "").replace("\u200f", "")
    t = re.sub(r"\[.*?\]", "", t)
    return t.lower()


def _has_any(text_n: str, tokens: tuple) -> bool:
    return any(tok in text_n for tok in tokens)


def extract_direction(text_n: str) -> str:
    if _has_any(text_n, SELL_TOKENS):
        return "SELL"
    if _has_any(text_n, BUY_TOKENS):
        return "BUY"
    return ""


def extract_numbers(text_n: str) -> list[int]:
    nums: list[int] = []
    for raw in re.findall(r"\d+(?:[.,]\d+)?", text_n):
        try:
            num = float(raw.replace(",", "."))
        except ValueError:
            continue
        if num.is_integer():
            nums.append(int(num))
    return nums


def _dedupe_preserve(nums: list[int]) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for n in nums:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def extract_params(text_n: str, direction: str) -> dict:
    """يستخرج الرقم الرئيسي ونقاط الستوب من نص الدخول."""
    numbers = _dedupe_preserve(extract_numbers(text_n))
    params: dict = {"price_level": "", "stop_points": 0}

    # رقم بجانب كلمة نقطة/ستوب = نقاط الستوب
    stop_match = re.search(r"(?:استوب|ستوب|stop)?\s*(\d{1,5})\s*(?:نقطه|نقطة|point)", text_n)
    stop_by_word = re.search(r"(?:استوب|ستوب)\s*(\d{1,5})", text_n)
    stop_points = 0
    if stop_match:
        stop_points = int(stop_match.group(1))
    elif stop_by_word:
        stop_points = int(stop_by_word.group(1))

    if not numbers:
        return params

    # الرقم الرئيسي: أول رقم لا يُستخدم كنقاط ستوب، ويفضّل أن > 10
    candidates = [n for n in numbers if n != stop_points]
    if not candidates:
        candidates = numbers
    price_candidates = [n for n in candidates if n > 10]
    chosen = price_candidates[0] if price_candidates else candidates[0]
    params["price_level"] = str(chosen)
    params["stop_points"] = stop_points
    return params


def classify(text: str = "", has_photo: bool = False) -> str:
    """يصنّف نص الرسالة إلى kind."""
    text_n = normalize(text)
    if not text_n:
        # صورة فقط بدون نص = دخول جديد (صورة الدخول هي الهوية)
        return "entry" if has_photo else "ignore"

    # أولوية الستوب على التأمين (كلمة لاتينية stop) ثم تأمين/نقاط ثم دخول
    if _has_any(text_n, STOP_TOKENS):
        return "stop"

    target_hit = _has_any(text_n, TARGET_TOKENS)
    entry_hit = _has_any(text_n, ENTRY_TOKENS) or _has_any(text_n, SELL_TOKENS + BUY_TOKENS)

    if target_hit and (entry_hit or has_photo):
        return "target"

    # ستوب قصير منفصل (بدون تأكيد) يُعامَل كستوب إن ذُكرت نقطة خسارة
    if "خساره" in text_n or "خسرانه" in text_n:
        return "stop"

    if entry_hit:
        return "entry"

    if has_photo:
        return "entry"

    return "ignore"


def build_entry_snapshot(text: str = "") -> dict:
    """علامات الدخول الجديد من نص الرسالة."""
    text_n = normalize(text)
    direction = extract_direction(text_n)
    params = extract_params(text_n, direction)
    return {
        "direction": direction,
        "price_level": params["price_level"],
        "stop_points": params["stop_points"],
        "condition_text": text.strip()[:1200] or None,
    }


def parse_followup(text: str = "", kind: str = "note") -> dict:
    """يستخرج value/price من رسالة المتابعة (ستوب/تأمين)."""
    text_n = normalize(text)
    numbers = _dedupe_preserve(extract_numbers(text_n))
    followup: dict = {"value": "", "price": ""}
    if kind == "stop":
        # الأرقام بجانب نقطة/ستوب هي قيم الخسارة
        stop_match = re.search(r"(?:استوب|ستوب|stop)?\s*(\d{1,5})\s*(?:نقطه|نقطة)", text_n)
        if stop_match:
            followup["value"] = stop_match.group(1)
        elif numbers:
            # أقرب رقم لأول كلمة ستوب
            followup["value"] = str(numbers[0])
            followup["price"] = str(numbers[-1])
    elif kind == "target":
        for i, n in enumerate(numbers):
            if n == 0:
                continue
            if i == 0:
                followup["value"] = str(n)
            else:
                followup["price"] = str(n)
                break
    elif numbers:
        followup["value"] = str(numbers[0])
        followup["price"] = str(numbers[-1])
    return followup