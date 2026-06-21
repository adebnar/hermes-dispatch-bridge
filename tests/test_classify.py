from app.classify import classify_schedule


def test_one_shot_when_no_recurrence():
    assert classify_schedule("summarize my inbox and draft replies").is_cron is False


def test_blank_is_one_shot():
    assert classify_schedule("").is_cron is False


def test_every_weekday_with_time():
    r = classify_schedule("every Monday at 9am send me a digest")
    assert r.is_cron and r.cron == "0 9 * * 1"


def test_daily_pm_time():
    r = classify_schedule("daily at 2pm give me a Gmail summary")
    assert r.is_cron and r.cron == "0 14 * * *"


def test_time_with_minutes():
    r = classify_schedule("every day at 2:30 pm do the thing")
    assert r.cron == "30 14 * * *"


def test_hourly():
    r = classify_schedule("every hour check the queue")
    assert r.cron == "0 * * * *"


def test_weekly_defaults_monday_9am():
    assert classify_schedule("weekly status report").cron == "0 9 * * 1"


def test_monthly_defaults_first_of_month():
    assert classify_schedule("monthly invoice summary").cron == "0 9 1 * *"


def test_midnight_am_normalization():
    r = classify_schedule("every day at 12am run backup")
    assert r.cron == "0 0 * * *"
