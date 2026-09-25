"""توليد أكواد تفعيل النسخ (أداة البائع فقط).

الاستخدام:
  نسخة تجريبية 30 يوماً و60 عضواً:
    python tools/make_license.py --kind trial --days 30 --members 60 --customer "Ali"

  نسخة دائمة (بلا انتهاء) و5000 عضو:
    python tools/make_license.py --kind permanent --members 5000 --customer "Buyer"

يرسل البائع الكود الناتج للمشتري، والمشتري يدخله في شاشة اختيار
«نسخة تجريبية / نسخة دائمة» عند أول فتحة للوحة.
يتطلب LICENSE_SECRET مضبوطاً في .env (أو متغيرات Railway) — نفس السر
الموجود عند الاستضافة التي أصدرت منه الأكواد.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config
from bot import license as lic


def main() -> int:
    parser = argparse.ArgumentParser(description="توليد كود تفعيل")
    parser.add_argument("--kind", required=True, choices=["trial", "permanent"])
    parser.add_argument("--days", type=int, default=0, help="أيام الصلاحية (0 = بلا انتهاء)")
    parser.add_argument("--members", type=int, default=0, help="الحد الأقصى للأعضاء")
    parser.add_argument("--customer", default="", help="اسم المشتري (اختياري)")
    args = parser.parse_args()

    if not config.LICENSE_SECRET:
        print("خطأ: LICENSE_SECRET غير مضبوط في .env أو متغيرات البيئة.", file=sys.stderr)
        print("أضفه أولاً ثم أعد المحاولة.", file=sys.stderr)
        return 2

    code = lic.generate_code(
        kind=args.kind,
        days=args.days,
        max_members=args.members,
        customer=args.customer,
        secret=config.LICENSE_SECRET,
    )
    print("=" * 58)
    print("كود التفعيل:")
    print()
    print(code)
    print()
    print(f"النوع: {'تجريبية' if args.kind == 'trial' else 'دائمة'}")
    if args.days > 0:
        print(f"الصلاحية: {args.days} يوماً")
    else:
        print("الصلاحية: بلا انتهاء")
    if args.members > 0:
        print(f"سقف الأعضاء: {args.members}")
    print(f"العميل: {args.customer or '-'}")
    print("=" * 58)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())