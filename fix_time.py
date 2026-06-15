from app import app, db, Review, BookViewLog
from datetime import timedelta

with app.app_context():
    # Исправляем время в рецензиях
    reviews = Review.query.all()
    for review in reviews:
        if review.created_at:
            review.created_at = review.created_at + timedelta(hours=3)

    # Исправляем время в логах просмотров
    logs = BookViewLog.query.all()
    for log in logs:
        if log.viewed_at:
            log.viewed_at = log.viewed_at + timedelta(hours=3)

    db.session.commit()
    print(f"✅ Исправлено {len(reviews)} рецензий и {len(logs)} записей просмотров")