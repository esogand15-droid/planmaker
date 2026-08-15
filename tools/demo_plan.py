"""Sample plans used by tests and the visual smoke test."""
from datetime import date
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.domain.models import Activity, Assignment, WeeklyPlan
from app.domain.persian import jalali_to_gregorian


def full_plan() -> WeeklyPlan:
    plan = WeeklyPlan(student_name="علی رضایی", student_id="8472", advisor_id="a1")
    plan.apply_week_start(jalali_to_gregorian(1405, 5, 25))
    data = {
        "saturday": [("زیست", "گوارش", "مطالعه + ۴۰ تست", "۹۰ دقیقه"),
                      ("ریاضی", "تابع", "تست ۱ تا ۳۰", "۶۰ دقیقه"),
                      ("فیزیک", "نوسان", "درسنامه", "۹۰ دقیقه"),
                      ("ادبیات", "آرایه‌ها", "۳۰ تست", "۴۵ دقیقه"),
                      ("زبان", "Reading", "2 passage", "40 min"),
                      ("شیمی", "استوکیومتری", "حل تمرین", "۶۰ دقیقه"),
                      ("دینی", "درس ۴", "مرور", "۳۰ دقیقه"),
                      ("مرور", "کل هفته", "جمع‌بندی", "۴۵ دقیقه")],
        "sunday": [("فیزیک", "دینامیک", "۵۰ تست", "۱۲۰ دقیقه"),
                    ("زیست", "ژنتیک", "مطالعه", "۹۰ دقیقه"),
                    ("عربی", "ترجمه", "۲۵ تست", "۴۵ دقیقه")],
        "monday": [("ریاضی", "حد و پیوستگی", "درسنامه + تست", "۱۲۰ دقیقه"),
                    ("شیمی Chemistry", "Chapter 3 - محلول‌ها", "مطالعه ۱۰۰٪ + ۴۰ تست زمان‌دار", "۹۰ دقیقه")],
        "tuesday": [("آزمون آزمایشی", "قلم‌چی", "برگزاری آزمون", "۴ ساعت"),
                     ("تحلیل آزمون", "", "بررسی خطاها", "۶۰ دقیقه")],
        "wednesday": [("زیست", "گیاهی", "۶۰ تست", "۹۰ دقیقه")],
        "thursday": [("جمع‌بندی", "", "مرور کل", "۱۸۰ دقیقه"),
                      ("استراحت", "", "", "")],
        "friday": [("مرور هفتگی", "همه دروس", "فلش‌کارت", "۱۲۰ دقیقه")],
    }
    for weekday, rows in data.items():
        day = plan.day(weekday)
        for i, (s, t, d, dur) in enumerate(rows):
            day.set_slot(i, Activity(i, subject=s, topic=t, description=d, duration=dur))
    for i, txt in enumerate(["مرور فصل ۲ زیست", "حل ۵۰ تست ریاضی", "تحلیل آزمون", "مرور لغات زبان"]):
        plan.assignments.append(Assignment(text=txt, order=i))
    return plan


def sparse_plan() -> WeeklyPlan:
    plan = WeeklyPlan(student_name="سارا محمدی")
    plan.apply_week_start(jalali_to_gregorian(1405, 6, 1))
    plan.day("saturday").set_slot(0, Activity(0, subject="زیست", topic="گوارش"))
    plan.day("monday").set_slot(3, Activity(3, subject="فیزیک", duration="۹۰ دقیقه"))
    plan.day("thursday").set_slot(7, Activity(7, description="تست زنی"))
    plan.assignments.append(Assignment(text="مرور فصل ۳", order=0))
    return plan
